#!/usr/bin/env bash
# 演示「规格更新后，上下文输出跟着更新」。
#
# 对应题面验收第 3 项的后半句：「规格更新并重新导入后，输出使用更新内容」。
#
# 做法：
#   1. 用初始规格构建一次上下文，存成 09-spec-update-before.json
#   2. 把 staged-updates/SPEC-001.updated.md 覆盖到 spec/SPEC-001.md
#   3. 重新导入
#   4. 再构建一次上下文，存成 09-spec-update-after.json
#   5. 断言：更新后的输出里出现了新增的 2.3 条款，且内容摘要变了
#
# 用法:
#   ./demo_spec_update.sh [python]
#
set -euo pipefail

PY="${1:-python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(dirname "$HERE")"
PROJECT="$MINIMAX_DIR/sample-project"
EVIDENCE="$MINIMAX_DIR/evidence"
TASK="为 TinyCalc 增加 min_value 筛选条件"

mkdir -p "$EVIDENCE"

echo "==> 1/5 备份初始规格"
cp "$PROJECT/spec/SPEC-001.md" "$EVIDENCE/spec-before.md"

echo "==> 2/5 用初始规格构建上下文"
cd "$MINIMAX_DIR"
"$PY" -m sdd context --task "$TASK" --evidence 09-spec-update-before.json >/dev/null

echo "==> 3/5 应用规格更新"
cp "$PROJECT/staged-updates/SPEC-001.updated.md" "$PROJECT/spec/SPEC-001.md"

echo "==> 4/5 重新导入"
"$PY" -m sdd ingest >/dev/null

echo "==> 5/5 用更新后的规格构建上下文"
"$PY" -m sdd context --task "$TASK" --evidence 09-spec-update-after.json >/dev/null

echo
echo "==> 对比"
"$PY" - "$EVIDENCE/09-spec-update-before.json" "$EVIDENCE/09-spec-update-after.json" <<'PYEOF'
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))

def describe(ctx):
    sections = [s["extra"]["section"] for s in ctx["specs"]]
    digests = sorted({s["content_digest"] for s in ctx["specs"] if s["content_digest"]})
    return sections, digests

b_sections, b_digests = describe(before)
a_sections, a_digests = describe(after)

print(f"更新前的规格条目: {b_sections}")
print(f"更新后的规格条目: {a_sections}")
print(f"更新前的规格摘要: {b_digests}")
print(f"更新后的规格摘要: {a_digests}")

if b_digests == a_digests:
    print("\n✗ 规格更新后摘要没变 —— 输出是固定答案，不满足验收第 3 项")
    raise SystemExit(1)

new_sections = [s for s in a_sections if s not in b_sections]
if not new_sections:
    print("\n✗ 更新后的输出没有出现新增的规格条款")
    raise SystemExit(1)

print(f"\n✓ 规格更新生效：新增条款 {new_sections}")
print("✓ 验收第 3 项「规格更新后输出跟着更新」通过")
PYEOF

echo
echo "说明：本轮把 spec/SPEC-001.md 改成了更新版。"
echo "      要恢复初始状态，执行："
echo "        git -C $PROJECT checkout -- spec/SPEC-001.md"
echo "        $PY -m sdd ingest"
