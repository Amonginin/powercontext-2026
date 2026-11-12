#!/usr/bin/env python3
"""Simulate the Dify workflow locally against a fake PowerContext.

The DSL is only worth shipping if its two code nodes actually run and the
memory round trip works. This executes the embedded Python of every `code`
node with representative inputs and checks the resulting graph, without
needing a Dify instance.

Usage:
    python3 simulate_dify_dsl.py <dsl.yml>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("需要 PyYAML", file=sys.stderr)
    raise SystemExit(2)


def find_node(doc, node_type):
    for node in doc["workflow"]["graph"]["nodes"]:
        if (node.get("data") or {}).get("type") == node_type:
            return node
    return None


def run_code_node(node, inputs):
    """Execute a Dify code node the way Dify does: define main(), call it."""
    code = node["data"]["code"]
    namespace: dict = {}
    exec(compile(code, node["id"], "exec"), namespace)
    main = namespace.get("main")
    if main is None:
        raise AssertionError(f"code node {node['id']} 没有定义 main()")
    # Dify passes the declared variables positionally, in declaration order.
    ordered = [v["variable"] for v in node["data"].get("variables") or []]
    args = [inputs.get(name) for name in ordered]
    return main(*args)


def main() -> int:
    path = Path(sys.argv[1])
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    graph = doc["workflow"]["graph"]
    nodes = {n["id"]: n for n in graph["nodes"]}

    print(f"== 模拟执行 {path.name} ==")

    # --- 1. the graph must be walkable from start ---
    reachable, stack = set(), ["start"]
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        for edge in graph["edges"]:
            if edge.get("source") == nid:
                stack.append(edge["target"])
    unreachable = set(nodes) - reachable
    if unreachable:
        print(f"  ✗ 有节点无法从 start 到达：{sorted(unreachable)}")
        return 1
    print(f"  ✓ 全部 {len(nodes)} 个节点均可从 start 到达")

    # --- 2. the two http nodes must target the PowerContext memory API ---
    urls = {}
    for nid, node in nodes.items():
        if node["data"]["type"] == "http-request":
            urls[nid] = node["data"]["url"]
    print(f"  http-request 目标：{sorted(set(urls.values()))}")
    for nid, url in urls.items():
        if "/v1/memory/search" not in url and "/v1/memory/remember" not in url:
            print(f"  ✗ http-request {nid} 没有指向 PowerContext 记忆接口")
            return 1
    if not any("/v1/memory/search" in u for u in urls.values()):
        print("  ✗ 缺少读取记忆的调用")
        return 1
    if not any("/v1/memory/remember" in u for u in urls.values()):
        print("  ✗ 缺少写入记忆的调用")
        return 1
    print("  ✓ 读取与写入记忆的调用都在")

    # --- 3. run the parse node against a realistic search response ---
    parse = find_node(doc, "code")
    code_nodes = [n for n in graph["nodes"] if n["data"]["type"] == "code"]
    parse = code_nodes[0]
    extract = code_nodes[1]

    search_body = json.dumps({
        "hits": [
            {"citation": {"entry_id": "e1"}, "text": "案件 A-17：玩家自称「老陈」，偏好直接下结论。",
             "score": 0.82, "matched_by": ["lexical"]},
            {"citation": {"entry_id": "e2"}, "text": "案件 A-17：已排除「地下室」方向。",
             "score": 0.51, "matched_by": ["lexical"]},
        ],
        "mode": "lexical",
    }, ensure_ascii=False)

    out = run_code_node(parse, {"body": search_body})
    print(f"  parse 输出：hit_count={out['hit_count']}")
    print(f"  记忆块预览：{out['memory_block'][:80]}")
    if out["hit_count"] != 2:
        print("  ✗ 解析节点没有正确解析 hits")
        return 1
    if "老陈" not in out["memory_block"]:
        print("  ✗ 记忆块丢失了原文内容")
        return 1

    # --- 4. an empty search must not break the flow ---
    empty = run_code_node(parse, {"body": json.dumps({"hits": []})})
    if "没有" not in empty["memory_block"]:
        print(f"  ✗ 空结果没有降级说明：{empty['memory_block']}")
        return 1
    print("  ✓ 空检索结果有降级说明")

    # --- 5. a malformed body must not raise ---
    broken = run_code_node(parse, {"body": "not json at all"})
    print(f"  ✓ 非法响应降级为：{broken['memory_block'][:40]}")

    # --- 6. run the extract node ---
    note = run_code_node(extract, {
        "npc_text": "你已经排除了地下室，我建议去看档案室。",
        "player_input": "我怀疑是档案室",
        "case_id": "A-17",
    })
    print(f"  extract 输出：{note['note'][:80]}")
    if "A-17" not in note["note"] or "档案室" not in note["note"]:
        print("  ✗ 提炼的记忆条目缺少关键信息")
        return 1

    # --- 7. the remember body must carry the note through ---
    save_node = nodes["save"]
    body_fields = {f["key"]: f["value"] for f in save_node["data"]["body"]["data"]}
    if "text" not in body_fields:
        print("  ✗ remember 请求体缺少 text 字段")
        return 1
    if body_fields["text"] != "{{#extract.note#}}":
        print(f"  ✗ remember 的 text 没有引用提炼结果：{body_fields['text']}")
        return 1
    for field, expected in (("scope_id", "{{#env.POWERCONTEXT_SCOPE_ID#}}"),
                            ("kind", "session")):
        if body_fields.get(field) != expected:
            print(f"  ✗ remember 的 {field} 取值不符：{body_fields.get(field)!r}")
            return 1
    print("  ✓ remember 请求体字段正确")

    # --- 8. the recall query must include the case id ---
    recall_node = nodes["recall"]
    recall_fields = {f["key"]: f["value"] for f in recall_node["data"]["body"]["data"]}
    q = recall_fields.get("query", "")
    if "{{#start.case_id#}}" not in q:
        print(f"  ✗ 检索 query 没有带上案件编号：{q}")
        return 1
    print("  ✓ 检索 query 带上了案件编号")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
