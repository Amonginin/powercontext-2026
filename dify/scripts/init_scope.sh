#!/usr/bin/env bash
# 初始化「记忆银行」需要的 PowerContext scope，并打印 scope_id。
#
# 幂等：重复执行会复用同一个 scope（idempotency_key 固定），
# 因此可以用它把环境恢复到初始状态。
#
# 用法:
#   POWERCONTEXT_URL=http://127.0.0.1:8000 ./init_scope.sh
#
# 输出:
#   SCOPE_ID=<scope id>          # 填进 Dify 的环境变量 POWERCONTEXT_SCOPE_ID
#
set -euo pipefail

PC_URL="${POWERCONTEXT_URL:-http://127.0.0.1:8000}"
PC_TOKEN="${POWERCONTEXT_TOKEN:-}"
SCOPE_KEY="${SCOPE_KEY:-dify-memory-vault}"
SCOPE_TITLE="${SCOPE_TITLE:-记忆银行 · 侦探存档}"
SCOPE_SUMMARY="${SCOPE_SUMMARY:-Dify 记忆银行应用使用的跨会话记忆作用域}"

if [[ -n "$PC_TOKEN" ]]; then
  AUTH=(-H "Authorization: Bearer ${PC_TOKEN}")
else
  AUTH=()
fi

echo "==> 等待 PowerContext 就绪 (${PC_URL})"
for _ in $(seq 1 30); do
  if curl -fsS "${AUTH[@]}" "${PC_URL}/health/ready" >/dev/null 2>&1; then
    echo "    就绪"
    break
  fi
  sleep 1
done

if ! curl -fsS "${AUTH[@]}" "${PC_URL}/health/ready" >/dev/null 2>&1; then
  echo "!! PowerContext 未就绪：${PC_URL}" >&2
  echo "   先按 docs/RUNBOOK.md 启动服务（powercontext server run）" >&2
  exit 1
fi

echo "==> 查找已有 scope（idempotency_key=${SCOPE_KEY}）"
existing="$(curl -fsS "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -X POST "${PC_URL}/v1/scopes" \
  -d "$(cat <<JSON
{
  "title": "${SCOPE_TITLE}",
  "summary": "${SCOPE_SUMMARY}",
  "idempotency_key": "${SCOPE_KEY}"
}
JSON
)")"

SCOPE_ID="$(printf '%s' "$existing" | sed -n 's/.*"scope_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"

if [[ -z "$SCOPE_ID" ]]; then
  # 兼容 scope 对象直接返回 id 的情况
  SCOPE_ID="$(printf '%s' "$existing" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
fi

if [[ -z "$SCOPE_ID" ]]; then
  echo "!! 无法从响应中解析 scope_id，原始响应：" >&2
  printf '%s\n' "$existing" >&2
  exit 1
fi

echo
echo "SCOPE_ID=${SCOPE_ID}"
echo
echo "把它填进 Dify 应用的环境变量："
echo "  POWERCONTEXT_SCOPE_ID = ${SCOPE_ID}"
echo
echo "重置为初始状态（清空该 scope 下的记忆）："
echo "  curl -X DELETE ${PC_URL}/v1/scopes/${SCOPE_ID}"
echo "  然后重新执行本脚本。"
