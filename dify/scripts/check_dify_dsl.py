#!/usr/bin/env python3
"""Validate a Dify app DSL before submitting it.

Dify refuses to import a DSL whose top level is missing `app`, whose version it
cannot parse, or whose graph references a node that does not exist. This checks
the parts that are cheap to verify locally, so a broken DSL never reaches the
reviewer.

Usage:
    python3 check_dify_dsl.py <dsl.yml> [...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the reviewer installs PyYAML
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

NODE_TYPES = {
    "start", "llm", "code", "tool", "answer", "end", "if-else", "assigner",
    "variable-aggregator", "template-transform", "http-request",
    "knowledge-retrieval-v2", "knowledge-retrieval", "parameter-extractor",
    "iteration", "loop", "custom-note", "constant", "variable", "doc-extractor",
    "question-classifier", "agent",
}

VAR_REF = re.compile(r"\{\{#([^#]+)#\}\}")
ENV_REF = re.compile(r"\{\{#env\.([A-Za-z0-9_]+)#\}\}")


def collect_refs(value, found):
    if isinstance(value, dict):
        for item in value.values():
            collect_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_refs(item, found)
    elif isinstance(value, str):
        for match in VAR_REF.finditer(value):
            found.add(match.group(1))


def check(path: Path) -> bool:
    print(f"== {path} ==")
    ok = True
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  ✗ 读取失败：{exc}")
        return False

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        print(f"  ✗ YAML 语法错误：{exc}")
        return False

    if not isinstance(doc, dict):
        print("  ✗ 顶层不是映射，Dify 无法导入")
        return False

    # --- top level ---
    if "app" not in doc:
        print("  ✗ 缺少顶层 app 段（Dify 源码要求 'the top-level app section is required'）")
        ok = False
    else:
        app = doc["app"]
        if not isinstance(app, dict):
            print("  ✗ app 不是映射")
            ok = False
        else:
            for key in ("name", "mode"):
                if key not in app:
                    print(f"  ✗ app 缺少 {key}")
                    ok = False
            mode = app.get("mode")
            if mode and mode not in {"workflow", "advanced-chat", "agent-chat"}:
                print(f"  ✗ app.mode 取值可疑：{mode}")
                ok = False

    if doc.get("kind") != "app":
        print(f"  ✗ kind 应为 app，实际 {doc.get('kind')!r}")
        ok = False

    version = doc.get("version")
    if version is None:
        print("  ✗ 缺少 version")
        ok = False
    else:
        try:
            minor = int(str(version).split(".")[1])
        except (ValueError, IndexError):
            print(f"  ✗ version 格式异常：{version!r}")
            minor = None
        if minor is not None and minor > 3:
            print(f"  ⚠ version {version} 较新，导入旧版 Dify 会进入 PENDING 状态")

    workflow = doc.get("workflow")
    if not isinstance(workflow, dict):
        print("  ✗ 缺少 workflow 段")
        return False

    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        print("  ✗ workflow.graph 缺失")
        return False

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        print("  ✗ graph.nodes 为空")
        ok = False
    if not edges:
        print("  ⚠ graph.edges 为空，工作流不可达")

    node_ids = set()
    node_types = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        data = node.get("data") or {}
        ntype = data.get("type")
        if not nid:
            print("  ✗ 存在没有 id 的节点")
            ok = False
            continue
        node_ids.add(str(nid))
        node_types[str(nid)] = ntype
        if ntype not in NODE_TYPES:
            print(f"  ✗ 节点 {nid} 的 type={ntype!r} 不在已知类型集合中")
            ok = False
        if ntype == "http-request":
            url = data.get("url")
            if not url:
                print(f"  ✗ http-request 节点 {nid} 缺少 url")
                ok = False
            body = data.get("body") or {}
            btype = body.get("type")
            if btype == "json":
                for field in body.get("data") or []:
                    if not field.get("key"):
                        print(f"  ✗ http-request {nid} 的 body 字段缺少 key")
                        ok = False
            if data.get("authorization", {}).get("type") == "api-key":
                print(f"  ⚠ http-request {nid} 使用 api-key 鉴权，请在环境变量里配占位值")
        if ntype == "code":
            if not data.get("code"):
                print(f"  ✗ code 节点 {nid} 缺少 code")
                ok = False
            if not data.get("outputs"):
                print(f"  ✗ code 节点 {nid} 缺少 outputs 声明")
                ok = False
        if ntype == "llm":
            model = data.get("model") or {}
            if not model.get("provider") or not model.get("name"):
                print(f"  ⚠ llm 节点 {nid} 未指定 provider/name，评审需自选模型")
            if not data.get("prompt_template"):
                print(f"  ✗ llm 节点 {nid} 缺少 prompt_template")
                ok = False

    # --- edges reference existing nodes ---
    for edge in edges:
        data = edge.get("data") or {}
        for side in ("source", "target"):
            ref = edge.get(side)
            if ref and str(ref) not in node_ids:
                print(f"  ✗ edge {edge.get('id')} 的 {side} 指向不存在的节点 {ref}")
                ok = False
        stype = data.get("sourceType")
        ttype = data.get("targetType")
        for ref, ntype, side in ((edge.get("source"), stype, "source"),
                                 (edge.get("target"), ttype, "target")):
            if ref and str(ref) in node_types:
                actual = node_types[str(ref)]
                if actual != ntype:
                    print(f"  ✗ edge {edge.get('id')} 声明 {side}Type={ntype}，"
                          f"但节点 {ref} 实际是 {actual}")
                    ok = False

    # --- variable references resolve ---
    refs = set()
    collect_refs(graph, refs)
    env_names = {v.get("name") for v in (workflow.get("environment_variables") or [])}
    for ref in sorted(refs):
        if ref.startswith("env."):
            name = ref[4:]
            if name not in env_names:
                print(f"  ✗ 引用了未定义的环境变量 env.{name}")
                ok = False
            continue
        if ref.startswith("sys."):
            continue
        node_id, _, var = ref.partition(".")
        if node_id not in node_ids:
            print(f"  ✗ 引用了不存在的节点 {node_id}（来自 {ref}）")
            ok = False
            continue
        ntype = node_types.get(node_id)
        if ntype == "http-request" and var == "body":
            continue
        if ntype == "start" and var:
            continue
        if ntype in {"llm", "code", "answer", "template-transform"}:
            continue

    # --- env var values are placeholders, not real credentials ---
    for var in workflow.get("environment_variables") or []:
        value = str(var.get("value") or "")
        name = var.get("name") or ""
        if name.endswith("TOKEN") or name.endswith("KEY") or name.endswith("SECRET"):
            if value and not value.startswith("replace-with") and "example" not in value:
                print(f"  ✗ 环境变量 {name} 看起来是真实凭据，必须改成占位值")
                ok = False
        if "scope_id" in name.lower() and value and not value.startswith("replace-with"):
            print(f"  ⚠ 环境变量 {name} 的值是具体 scope id，评审环境需要自己填")

    print(f"  节点 {len(nodes)} 个，边 {len(edges)} 条，"
          f"引用 {len(refs)} 处，环境变量 {len(env_names)} 个")
    print("  ✓ 通过" if ok else "  ✗ 存在问题")
    print()
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 check_dify_dsl.py <dsl.yml> [...]")
        return 2
    all_ok = True
    for arg in sys.argv[1:]:
        all_ok = check(Path(arg)) and all_ok
    if all_ok:
        print("全部通过")
        return 0
    print("存在问题")
    return 1


if __name__ == "__main__":
    sys.exit(main())
