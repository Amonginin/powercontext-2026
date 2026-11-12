# 运行说明 · 规有所依

> MiniMax 赛题提交材料「运行说明」：依赖版本、配置步骤、数据初始化、调用入口、
> 测试命令及已知限制。

---

## 一、依赖版本

| 组件 | 版本要求 | 说明 |
|---|---|---|
| Python | ≥ 3.11 | 开发与验证用 3.13 |
| pytest | ≥ 8 | 跑样例工程的测试 |
| pymysql | ≥ 1.1 | 连 seekdb / OceanBase（MySQL 协议） |
| PowerContext | 本仓库 `src/`（分支 `2026-competition-ecosystem`） | 存任务状态 |
| seekdb 或 OceanBase | seekdb 任意发行版 / OceanBase V4+ | 存工程资料 |
| MiniMax | `MiniMax-M3.1-Flash`（或 `MiniMax-M3`） | 仅「改代码」这一步需要 |

`sdd` 模块本身**只用标准库 + pymysql**，没有别的第三方依赖。

### 安装

```bash
# 1) PowerContext（提供状态存储）
git clone https://github.com/Amonginin/powercontext-2026.git
cd powercontext-2026 && git checkout 2026-competition-ecosystem
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[server,builtin]"

# 2) 样例工程与模块的依赖
pip install pytest pymysql

# 3) MiniMax SDK 不需要 —— 模块直接用 HTTP 调 OpenAI 兼容端点
```

---

## 二、配置步骤

全部通过环境变量，**配置文件里没有任何真实凭据**。

```bash
# --- PowerContext（任务状态）---
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_SCOPE_ID=            # 由 `python -m sdd init` 产出
export POWERCONTEXT_TOKEN=               # 默认 loopback 不启用鉴权，留空

# --- seekdb / OceanBase（工程资料）---
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
export SEEKDB_USER=root
export SEEKDB_PASSWORD=
export SEEKDB_DATABASE=sdd_context

# --- MiniMax（只有改代码这一步需要）---
export MINIMAX_API_KEY=sk-...
export MINIMAX_BASE_URL=https://api.minimax.io/v1    # 国内账号用 https://api.minimaxi.com/v1
export MINIMAX_MODEL=MiniMax-M3.1-Flash
export MINIMAX_EFFORT=medium
```

> **MiniMax 的区域必须和账号一致。** 国际站用 `api.minimax.io`，中国站用 `api.minimaxi.com`；
> key 跨区会返回 401。模块走 OpenAI 兼容端点（`POST /v1/chat/completions`）。
>
> **MiniMax 不能关闭思考。** 传 `thinking: {"type":"disabled"}` 会返回 400，
> 所以模块不传这个字段，只传 `reasoning_effort`。思考内容在 `reasoning_content` 里，
> 回答在 `content` 里，两者都会存进证据。

---

## 三、数据初始化

### 1. 起 seekdb

```bash
# 有现成的 seekdb 可执行文件时
bash minimax/scripts/setup_seekdb.sh

# 或手动：
seekdb --base-dir /tmp/sdb_sdd --port 2881 --nodaemon &
obclient -h127.0.0.1 -P2881 -uroot --protocol=tcp \
  -e "CREATE DATABASE IF NOT EXISTS sdd_context CHARACTER SET utf8mb4;"
```

> 脚本会复用已占用端口的实例，不会重复启动；起不来时打印 `server.log` 末尾。

### 2. 起 PowerContext

```bash
powercontext server run --host 127.0.0.1 --port 8000
# 另开一个终端确认：
curl -s http://127.0.0.1:8000/health/ready
```

### 3. 建表 + 建 scope

```bash
cd minimax
python -m sdd init
```

输出：

```
✓ 数据库表已就绪
✓ scope 已创建：scp_xxxxxxxxxxxxxxxx
```

把 `scp_...` 填进 `POWERCONTEXT_SCOPE_ID`。

---

## 四、调用入口

模块只提供一个 CLI：`python -m sdd <子命令>`。

| 子命令 | 作用 |
|---|---|
| `init` | 建表 + 建 scope |
| `ingest` | 把 `sample-project` 的规格/代码/测试导入 seekdb |
| `context --task "..."` | **构建开发上下文**（核心入口），`--evidence` 可落盘 |
| `agent --task "..."` | 调 MiniMax 生成补丁 |
| `apply` | 把补丁应用到样例工程 |
| `verify --phase before\|after\|regression` | 跑测试并分类结果 |
| `state --save\|--show` | 保存 / 读回任务状态 |
| `db-query` | 查询工程资料库并记录 |

其它形式（供参考，未作为主路径）：
- `context` 的输出是 JSON，可被任何前端消费；
- PowerContext 侧的状态通过 HTTP API 可读（`POST /v1/memory/search`）。

---

## 五、完整流程（对应题面 2.1 的六步）

```bash
cd minimax
bash scripts/run_all.sh            # 一键跑完
```

或分步：

```bash
python -m sdd ingest                                     # ① 导入资料
python -m sdd context --task "为 TinyCalc 增加 min_value 筛选条件"   # ② 构建上下文
python -m sdd verify --phase before                      # （改前基线）
python -m sdd agent --task "为 TinyCalc 增加 min_value 筛选条件"     # ③ MiniMax 改代码
python -m sdd apply                                      #    应用补丁
python -m sdd verify --phase after                       # ④ 目标测试
python -m sdd verify --phase regression -- tests/test_filters.py    # ④ 回归测试
python -m sdd state --task "..." --save                  # ⑤ 保存状态
# 重启 PowerContext，再：
python -m sdd state --task "..." --show                   # ⑤ 读回状态
```

---

## 六、测试命令

```bash
# 样例工程（评审要跑的就是这两条）
cd minimax/sample-project
pytest tests/test_calculator.py -v      # 目标测试
pytest tests/ -q                        # 全量（含回归）

# 模块自身的单测
cd minimax
pytest tests/test_sdd_module.py -q      # 27 项
```

---

## 七、网络与资源

- PowerContext 与 seekdb 都在本机时无额外网络要求。
- MiniMax 需要能访问 `api.minimax.io`（或 `api.minimaxi.com`）。
  这一条**只在 `sdd agent` 这一步需要**，其余步骤离线可跑。
- 资源：seekdb 最小 1 核 2G；PowerContext 1 核 1G；样例工程与模块本身可忽略。

---

## 八、已知限制

1. **`sdd agent` 需要 `MINIMAX_API_KEY`**。没有 key 时其余五步照常运行，
   脚本会明确说明跳过了哪一步。开发记录与补丁已经提交在 `evidence/` 下。
2. **上下文检索是词面匹配（`LIKE`），不是向量检索。** 模块刻意用数据库自带的匹配，
   好处是「查询结果真的来自数据库」可被逐条核对；代价是任务描述里的词要和规格有重叠。
   换成向量检索只需替换 `database.py` 的两个 `search_*` 方法。
3. **规格条款按 Markdown 二级/三级标题切分。** 没有标题的规格不会被切成条款，
   但整份文件仍会作为 `spec` 资料入库。
4. **`staged-updates/` 默认不入库。** 它存的是「改了之后的规格」，只在演示
   `demo_spec_update.sh` 时被复制进 `spec/`，否则新旧两版会同时命中同一个查询。
5. **Windows 上跑不了 seekdb**（`pylibseekdb` 只支持 Linux/macOS）。
   SQL 层用 MySQL 8.0 验证（同为 MySQL 协议），在生产/评审的 Linux 环境上
   把 `SEEKDB_*` 指向 seekdb 即可，代码不用改。
