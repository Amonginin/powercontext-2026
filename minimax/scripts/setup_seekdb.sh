#!/usr/bin/env bash
# 在 Linux 上准备 seekdb，并让 SDD 模块连过去。
#
# 本机（Windows）跑不了 seekdb，所以工程资料落在 seekdb 的验证在 Linux 上做。
# 这个脚本做三件事：把 seekdb 起起来、建库、把连接方式打印出来。
#
# 用法（在仓库根目录）:
#   bash minimax/scripts/setup_seekdb.sh
#
# 覆盖项:
#   SEEKDB_BIN     seekdb 可执行文件路径
#   SEEKDB_BASE    seekdb 数据目录
#   SEEKDB_PORT    端口（默认 2881）
#
set -euo pipefail

SEEKDB_BIN="${SEEKDB_BIN:-/tmp/seekdb/build_release/src/observer/seekdb}"
SEEKDB_BASE="${SEEKDB_BASE:-/tmp/sdb_sdd}"
SEEKDB_PORT="${SEEKDB_PORT:-2881}"
DB_NAME="${SEEKDB_DATABASE:-sdd_context}"

echo "==> 检查 seekdb 可执行文件"
if [[ ! -x "$SEEKDB_BIN" ]]; then
  echo "!! 找不到可执行文件：$SEEKDB_BIN" >&2
  echo "   如果还没编译，先跑：bash tools/comp_recover.sh restore && bash tools/comp_recover.sh build" >&2
  echo "   或者用 SEEKDB_BIN=/path/to/seekdb 指定路径。" >&2
  exit 1
fi
"$SEEKDB_BIN" --version 2>/dev/null | head -1 || true

echo "==> 检查端口 ${SEEKDB_PORT}"
if (exec 3<>/dev/tcp/127.0.0.1/"${SEEKDB_PORT}") 2>/dev/null; then
  echo "    端口已被占用，复用现有实例"
  STARTED=0
else
  echo "==> 启动 seekdb（数据目录 ${SEEKDB_BASE}）"
  rm -rf "$SEEKDB_BASE"
  mkdir -p "$SEEKDB_BASE"
  nohup "$SEEKDB_BIN" --base-dir "$SEEKDB_BASE" --port "$SEEKDB_PORT" --nodaemon \
    > "$SEEKDB_BASE/server.log" 2>&1 &
  STARTED=1
fi

echo "==> 等待端口可连"
for _ in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/"${SEEKDB_PORT}") 2>/dev/null; then
    echo "    已就绪"
    break
  fi
  sleep 1
done

if ! (exec 3<>/dev/tcp/127.0.0.1/"${SEEKDB_PORT}") 2>/dev/null; then
  echo "!! seekdb 没有起来。server.log 末尾：" >&2
  tail -30 "$SEEKDB_BASE/server.log" >&2 || true
  exit 1
fi

echo "==> 找 MySQL 客户端"
MYSQL_CLI=""
for candidate in \
  "$(dirname "$SEEKDB_BIN")/obclient" \
  /tmp/seekdb/deps/3rd/usr/bin/obclient \
  "$(command -v obclient || true)" \
  "$(command -v mysql || true)"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    MYSQL_CLI="$candidate"
    break
  fi
done
if [[ -z "$MYSQL_CLI" ]]; then
  echo "!! 找不到 obclient / mysql 客户端" >&2
  exit 1
fi
echo "    使用 $MYSQL_CLI"

echo "==> 建库 ${DB_NAME}"
"$MYSQL_CLI" -h127.0.0.1 -P"${SEEKDB_PORT}" -uroot --protocol=tcp \
  --default-character-set=utf8mb4 \
  -e "CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4; SHOW DATABASES;"

echo "==> 确认 Python 驱动"
python3 -c "import pymysql" 2>/dev/null || python3 -m pip install --quiet pymysql

echo
echo "=============================================="
echo "seekdb 就绪。接下来导出这几个环境变量："
echo
echo "  export SEEKDB_HOST=127.0.0.1"
echo "  export SEEKDB_PORT=${SEEKDB_PORT}"
echo "  export SEEKDB_USER=root"
echo "  export SEEKDB_PASSWORD="
echo "  export SEEKDB_DATABASE=${DB_NAME}"
echo
echo "然后跑完整链路："
echo "  cd minimax && python3 -m sdd ingest && python3 -m sdd context --task '为 TinyCalc 增加 min_value 筛选条件'"
echo "=============================================="
echo
echo "（本次 seekdb 是脚本启动的：STARTED=${STARTED}）"
