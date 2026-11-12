#!/usr/bin/env bash
# 一键跑完 SDD 上下文模块的完整链路。
#
# 阶段的顺序就是题面 2.1 的六步流程：
#   ① 导入任务涉及的规格、代码和测试资料
#   ② 根据任务选择相关资料，形成开发上下文
#   ③ 把上下文交给使用 MiniMax 的开发助手，完成代码修改
#   ④ 运行功能与回归测试，验证修改结果
#   ⑤ 保存任务状态，重启后读取并使用
#
# 其中 ③ 需要 MINIMAX_API_KEY。没有 key 时脚本会停在 ③ 之前，
# 并把已经产出的证据保留下来 —— 其余步骤都不依赖 key。
#
# 用法:
#   export POWERCONTEXT_SCOPE_ID=scp_xxx
#   ./run_all.sh                    # 跑 ① ②，然后尝试 ③
#   SKIP_AGENT=1 ./run_all.sh       # 只跑不依赖 key 的部分
#
set -euo pipefail

PY="${PYTHON:-python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(dirname "$HERE")"
PROJECT="$MINIMAX_DIR/sample-project"
EVIDENCE="$MINIMAX_DIR/evidence"
TASK="${TASK:-为 TinyCalc 增加 min_value 筛选条件}"

cd "$MINIMAX_DIR"
mkdir -p "$EVIDENCE"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "0/6 检查环境"
: "${POWERCONTEXT_SCOPE_ID:?需要设置 POWERCONTEXT_SCOPE_ID，先运行 python -m sdd init}"
echo "PowerContext: ${POWERCONTEXT_URL:-http://127.0.0.1:8000}"
echo "scope: $POWERCONTEXT_SCOPE_ID"
echo "seekdb: ${SEEKDB_HOST:-127.0.0.1}:${SEEKDB_PORT:-2881}/${SEEKDB_DATABASE:-sdd_context}"
"$PY" - <<'PYEOF'
import json, os, urllib.request
url = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/") + "/health/ready"
try:
    with urllib.request.urlopen(url, timeout=10) as r:
        print("PowerContext 就绪:", json.loads(r.read().decode())["status"])
except Exception as exc:
    raise SystemExit(f"PowerContext 未就绪（{url}）：{exc}")
PYEOF

step "1/6 导入工程资料到 seekdb / OceanBase"
"$PY" -m sdd ingest | tail -20

step "2/6 构建开发上下文"
"$PY" -m sdd context --task "$TASK" --evidence 02-retrieve-context.json >/dev/null
echo "✓ 上下文写入 evidence/02-retrieve-context.json"

step "3/6 记录数据库查询（验收第 4 项）"
"$PY" -m sdd db-query | tail -12

step "4/6 保存任务状态"
"$PY" -m sdd state --task "$TASK" --save \
  --done "导入规格、代码与测试资料" \
  --done "构建开发上下文" \
  --todo "生成补丁并应用" \
  --verification "改前目标测试因功能未实现而失败" | tail -20

if [[ "${SKIP_AGENT:-0}" == "1" ]]; then
  step "5/6 跳过 MiniMax（SKIP_AGENT=1）"
else
  if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
    step "5/6 跳过 MiniMax（未设置 MINIMAX_API_KEY）"
    echo "代码修改这一步需要真实的 MiniMax 密钥。"
    echo "其余步骤都不需要它，已有证据保留在 evidence/ 下。"
  else
    step "5/6 调用 MiniMax 生成补丁"
    "$PY" -m sdd agent --task "$TASK"
    step "应用补丁"
    "$PY" -m sdd apply
    step "跑改后测试"
    "$PY" -m sdd verify --phase after
    step "跑回归测试"
    "$PY" -m sdd verify --phase regression -- tests/test_filters.py
    step "更新任务状态"
    "$PY" -m sdd state --task "$TASK" --save \
      --done "导入规格、代码与测试资料" \
      --done "构建开发上下文" \
      --done "MiniMax 生成补丁并应用" \
      --verification "改后目标测试全部通过" \
      --verification "回归测试全部通过"
  fi
fi

step "6/6 完成"
ls -1 "$EVIDENCE"
