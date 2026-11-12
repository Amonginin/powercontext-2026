"""Command line entry point of the SDD context module.

    python -m sdd init                 create the schema and the scope
    python -m sdd ingest               load the project into seekdb/OceanBase
    python -m sdd context --task "..."  build the development context
    python -m sdd agent --task "..."    ask MiniMax for a patch
    python -m sdd apply                apply the patch to the sample project
    python -m sdd verify --before      run the tests and classify the result
    python -m sdd state --save         persist the task state
    python -m sdd state --show         read the task state back
    python -m sdd db-query              run a query against the material store
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import MiniMaxAssistant, MiniMaxError
from .config import load_settings
from .database import DatabaseUnavailableError, MaterialStore
from .ingest import ingest_project
from .powercontext import PowerContextClient, PowerContextError
from .retrieve import build_context
from .state import StateStore, TaskState, task_key_for
from .verify import render_log, run_pytest

__all__ = ["main"]

_DEFAULT_SCOPE_TITLE = "SDD 上下文 · 规有所依"
_DEFAULT_SCOPE_SUMMARY = "SDD 上下文管理系统使用的工程资料与任务状态作用域"
_DEFAULT_SCOPE_KEY = "sdd-context-module"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent / "sample-project"


def _evidence_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "evidence"


def _write_evidence(name: str, content: str) -> Path:
    directory = _evidence_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _store() -> MaterialStore:
    return MaterialStore(load_settings())


def _client() -> PowerContextClient:
    return PowerContextClient(load_settings())


def _scope_id(settings) -> str:
    if not settings.powercontext_scope_id:
        raise SystemExit(
            "缺少 POWERCONTEXT_SCOPE_ID。先运行 `python -m sdd init` 并把打印出来的 scope id 填进环境变量。"
        )
    return settings.powercontext_scope_id


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def cmd_init(args) -> int:
    settings = load_settings()
    store = _store()
    try:
        store.ensure_schema()
    except DatabaseUnavailableError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2
    print("✓ 数据库表已就绪")

    client = _client()
    try:
        scope_id = client.create_scope(
            _DEFAULT_SCOPE_TITLE,
            _DEFAULT_SCOPE_SUMMARY,
            args.scope_key or _DEFAULT_SCOPE_KEY,
        )
    except PowerContextError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2
    print(f"✓ scope 已创建：{scope_id}")
    print()
    print("把它填进环境变量：")
    print(f"  POWERCONTEXT_SCOPE_ID = {scope_id}")
    return 0


def cmd_ingest(args) -> int:
    settings = load_settings()
    store = _store()
    project = Path(args.project) if args.project else _project_root()
    project = project.resolve()

    try:
        store.ensure_schema()
        results = ingest_project(store, project)
    except DatabaseUnavailableError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    lines = ["# 导入工程资料到 seekdb / OceanBase", ""]
    lines.append(f"project_root: {project}")
    lines.append(f"scope_id: {settings.powercontext_scope_id or '(not set)'}")
    lines.append(f"database: {settings.database_host}:{settings.database_port}"
                 f"/{settings.database_name}")
    lines.append("")
    lines.append("material_id | role | path | content_digest | clauses")
    lines.append("---|---|---|---|---")
    for item in results:
        lines.append(
            f"{item.material_id} | {item.role} | {item.path} | "
            f"{item.content_digest} | {item.clause_count}"
        )
    lines.append("")
    lines.append(f"共导入 {len(results)} 份资料。")

    path = _write_evidence("01-ingest.log", "\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n✓ 已写入 {path}")
    return 0


def cmd_context(args) -> int:
    settings = load_settings()
    store = _store()

    state_payload = None
    scope_id = settings.powercontext_scope_id
    if scope_id:
        try:
            state = StateStore(store, _client(), scope_id).load(args.task)
            if state is not None:
                state_payload = state.to_dict()
        except (PowerContextError, DatabaseUnavailableError):
            state_payload = None

    context = build_context(store, args.task, state=state_payload)
    payload = context.to_dict()

    if args.output:
        Path(args.output).write_text(context.to_json() + "\n", encoding="utf-8")
        print(f"✓ 已写入 {args.output}")
    else:
        print(context.to_json())

    if args.evidence:
        path = _write_evidence(
            args.evidence, context.to_json() + "\n"
        )
        print(f"✓ 已写入 {path}")
    return 0


def cmd_agent(args) -> int:
    settings = load_settings()
    store = _store()
    scope_id = _scope_id(settings)

    state = StateStore(store, _client(), scope_id).load(args.task)
    context = build_context(
        store, args.task, state=state.to_dict() if state else None
    )

    prompt = context.as_prompt()
    _write_evidence("03-minimax-prompt.txt", prompt)

    assistant = MiniMaxAssistant(settings)
    try:
        response, diff = assistant.propose_patch(prompt)
    except MiniMaxError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    request_record = {
        "base_url": settings.minimax_base_url,
        "model": settings.minimax_model,
        "reasoning_effort": settings.minimax_effort,
        "api_key": "***redacted***",
        "system_prompt": assistant.__class__.__module__ and None,
    }
    request_record.pop("system_prompt")
    request_record["context_sources"] = [
        s.identifier for s in (context.specs + context.code + context.tests)
    ]
    _write_evidence(
        "03-minimax-request.json", json.dumps(request_record, ensure_ascii=False, indent=2) + "\n"
    )
    _write_evidence(
        "03-minimax-response.json", json.dumps(response.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    if diff:
        _write_evidence("04-patch.diff", diff)

    print(f"request_id: {response.request_id}")
    print(f"model: {response.model}")
    print(f"usage: {response.usage}")
    print(f"补丁长度: {len(diff)} 字节")
    if not diff:
        print("!! 模型没有返回可解析的 diff", file=sys.stderr)
        return 1
    return 0


def cmd_apply(args) -> int:
    import subprocess

    project = (Path(args.project) if args.project else _project_root()).resolve()
    patch_path = Path(args.patch) if args.patch else _evidence_dir() / "04-patch.diff"
    if not patch_path.exists():
        print(f"!! 找不到补丁文件：{patch_path}", file=sys.stderr)
        return 2

    check = subprocess.run(
        ["git", "apply", "--check", "-p1", str(patch_path)],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check.returncode != 0:
        print(f"!! 补丁无法应用：{check.stderr.strip()}", file=sys.stderr)
        return 1

    applied = subprocess.run(
        ["git", "apply", "-p1", str(patch_path)],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if applied.returncode != 0:
        print(f"!! 应用失败：{applied.stderr.strip()}", file=sys.stderr)
        return 1
    print("✓ 补丁已应用")
    return 0


def cmd_verify(args) -> int:
    settings = load_settings()
    project = (Path(args.project) if args.project else _project_root()).resolve()
    targets = args.targets or None

    result = run_pytest(project, targets=targets, revision=args.revision or "")
    heading = {
        "before": "改前测试结果",
        "after": "改后测试结果",
        "regression": "回归测试结果",
    }.get(args.phase, args.phase)
    log = render_log(result, heading)

    name = {
        "before": "05-test-before.log",
        "after": "05-test-after.log",
        "regression": "05-test-regression.log",
    }.get(args.phase, f"05-test-{args.phase}.log")
    path = _write_evidence(name, log)

    print(log)
    print(f"✓ 已写入 {path}")

    if args.phase == "before":
        if not result.is_missing_feature:
            print(
                "!! 改前的失败不是「功能未实现」形态，不能作为验收证据。"
                f" 分类={result.category}",
                file=sys.stderr,
            )
            return 1
    elif result.exit_code != 0:
        print("!! 改后仍有测试失败", file=sys.stderr)
        return 1
    return 0


def cmd_state(args) -> int:
    settings = load_settings()
    store = _store()
    scope_id = _scope_id(settings)
    state_store = StateStore(store, _client(), scope_id)

    if args.show:
        state = state_store.load(args.task)
        if state is None:
            print("没有找到已保存的状态。")
            return 1
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        _write_evidence(
            "07-powercontext-state.json",
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
        return 0

    if args.save:
        state = state_store.load(args.task)
        if state is None:
            state = TaskState(task=args.task, task_key=task_key_for(args.task))
        if args.done:
            state.done = args.done
        if args.todo:
            state.todo = args.todo
        if args.verification:
            state.verification = args.verification
        payload = state_store.save(state)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        _write_evidence(
            "07-powercontext-state.json",
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        return 0

    print("请指定 --save 或 --show", file=sys.stderr)
    return 2


def cmd_db_query(args) -> int:
    """Run a query against the engineering-material store and log it."""
    settings = load_settings()
    store = _store()
    try:
        store.ensure_schema()
        materials = store.list_material(args.role)
    except DatabaseUnavailableError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    lines = [
        "# 数据库查询记录",
        "",
        f"host: {settings.database_host}:{settings.database_port}",
        f"database: {settings.database_name}",
        "",
        f"$ SELECT material_id, role, path, content_digest FROM sdd_material"
        + (f" WHERE role = '{args.role}'" if args.role else ""),
    ]
    for row in materials:
        lines.append(f"{row.material_id} | {row.role} | {row.path} | {row.content_digest}")

    if args.role in (None, "spec"):
        lines += ["", "$ SELECT clause_id, spec_id, section FROM sdd_clause ORDER BY clause_id"]
        for clause in store.search_clauses(["min_value", "min", "筛选", "apply", "filter"]):
            lines.append(f"{clause.clause_id} | {clause.spec_id} | {clause.section}")

    path = _write_evidence("06-db-queries.log", "\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n✓ 已写入 {path}")
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdd",
        description="SDD 上下文管理系统：规格驱动的开发上下文构建与任务状态管理",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="创建数据库表与 PowerContext scope")
    p_init.add_argument("--scope-key", default=None, help="scope 的幂等键")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="把工程资料导入 seekdb / OceanBase")
    p_ingest.add_argument("--project", default=None, help="项目根目录，默认 sample-project")
    p_ingest.set_defaults(func=cmd_ingest)

    p_context = sub.add_parser("context", help="构建开发上下文")
    p_context.add_argument("--task", required=True, help="开发任务描述")
    p_context.add_argument("--output", default=None, help="把 JSON 写到文件")
    p_context.add_argument("--evidence", default=None, help="把 JSON 写到 evidence 目录")
    p_context.set_defaults(func=cmd_context)

    p_agent = sub.add_parser("agent", help="调用 MiniMax 生成补丁")
    p_agent.add_argument("--task", required=True, help="开发任务描述")
    p_agent.set_defaults(func=cmd_agent)

    p_apply = sub.add_parser("apply", help="把补丁应用到样例工程")
    p_apply.add_argument("--project", default=None, help="项目根目录")
    p_apply.add_argument("--patch", default=None, help="补丁文件路径")
    p_apply.set_defaults(func=cmd_apply)

    p_verify = sub.add_parser("verify", help="运行测试并分类结果")
    p_verify.add_argument("--phase", default="before", choices=["before", "after", "regression"])
    p_verify.add_argument("--project", default=None, help="项目根目录")
    p_verify.add_argument("--revision", default="", help="本次运行的代码版本标识")
    p_verify.add_argument("targets", nargs="*", help="测试目标，默认整个 tests 目录")
    p_verify.set_defaults(func=cmd_verify)

    p_state = sub.add_parser("state", help="保存或读取任务状态")
    p_state.add_argument("--task", required=True, help="开发任务描述")
    p_state.add_argument("--save", action="store_true", help="写入状态")
    p_state.add_argument("--show", action="store_true", help="读回状态")
    p_state.add_argument("--done", action="append", default=None, help="已完成事项")
    p_state.add_argument("--todo", action="append", default=None, help="待办事项")
    p_state.add_argument("--verification", action="append", default=None, help="已有验证结果")
    p_state.set_defaults(func=cmd_state)

    p_db = sub.add_parser("db-query", help="查询工程资料库并记录")
    p_db.add_argument("--role", default=None, help="只查某个角色：spec / code / test")
    p_db.set_defaults(func=cmd_db_query)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
