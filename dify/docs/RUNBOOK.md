# 运行说明 · 记忆银行（Memory Vault）

> Dify 赛题提交材料 4.2。本文件覆盖题面要求的四项：
> ①作品创意/玩法/核心功能/体验方式 + Dify 与指定技术的分工；
> ②运行环境、软件和插件版本、所需模型服务及配置项；
> ③从安装依赖 → 导入 DSL → 初始化数据 → 启动应用的完整步骤 + 应用入口；
> ④外部服务的获取和配置方式、Dify 访问服务所需网络条件、必要资源条件、已知功能限制。

---

## 一、作品创意

### 一句话

**「记忆银行」是一个跨会话的侦探 NPC**：它把玩家说过的话、排除过的线索、偏好的说话风格
存进 PowerContext 记忆库；**下一次开新会话，它依然认得你**。

### 玩法

你扮演一位调查者，NPC 是代号「档案员」的侦探。你每轮提交一条推理，
档案员会结合**它记忆库里的你**来回应——用你偏好的称呼、避开你已经排除过的方向。

核心张力在于：**档案员的「了解你」不是伪装出来的，是真的从数据库里读出来的**。
你在上一局说「别再提地下室了」，下一局它真的不再提。

### 核心功能

| 功能 | 说明 |
|---|---|
| 跨会话记忆召回 | 每次开场先从 PowerContext 检索与本案相关的既往记录，注入 NPC 的 system prompt |
| 记忆写入 | 每轮结束后把「玩家输入 + 侦探回应」提炼成一条记忆写回 PowerContext |
| 记忆感知的对话 | NPC 的称呼、语气、避讳都来自召回结果；记忆为空时它会自然地说「初次合作」 |
| 降级不崩 | 检索为空、响应非法、PowerContext 未启动，都有明确兜底文案，不会静默失败 |

### 体验方式

直接用 **Dify 自带的 Web App**（发布后得到 `http://<host>/chat/<app-key>`），
或调用 Dify 的 **API**（`/chat-messages` / `/workflows/run`）。**无需自研前端**。

单轮输入三个变量：

- `case_id`：案件编号（同时是记忆的检索键，决定召回哪些记录）
- `player_name`：你希望被怎么称呼
- `player_input`：本轮推理

---

## 二、Dify 与 PowerContext 的分工

| 层 | 承担什么 | 依据 |
|---|---|---|
| **Dify（必选）** | **核心流程编排**：接收输入与变量（Start）→ 调用外部记忆接口（HTTP Request）→ 解析返回并组装提示词（Code）→ 模型推理生成角色回复（LLM）→ 提炼记忆（Code）→ 写回记忆库（HTTP Request）→ 输出给玩家（Answer） | 题面 2.2：Dify 承担「输入处理、模型调用、检索或工具调用、条件判断和结果输出」 |
| **PowerContext** | **记忆的保存与复用**：提供 `POST /v1/memory/search`（召回）与 `POST /v1/memory/remember`（写入）。召回结果**直接注入 LLM 的 system prompt**，写入内容是**玩家本轮的真实输入**，不是固定示例文本 | 题面 2.2：PowerContext「保存并复用应用中的记忆或上下文，例如用户偏好、角色状态、剧情进展和历史互动」 |

**PowerContext 在这里是不可替代的**：去掉它，NPC 的「跨会话记忆」整个塌掉，
应用退化成普通单轮对话。这正是题面「技术结合有效」要求的「实际发挥作用」。

> 按题面「选择 PowerContext 的作品不额外要求使用 seekdb」，本作品**只使用 PowerContext**
> 一种指定技术即满足技术选型门槛。PowerContext 默认以 SQLite 持久化，符合赛事 FAQ
> 「PowerContext 自身使用 SQLite 持久化符合要求」。

### 节点流程

**单链结构**，没有并行分支：

```
Start(案件编号 / 称呼 / 推理输入)
  └─► HTTP  召回记忆   POST {PC_URL}/v1/memory/search
        └─► Code 解析记忆   解析 hits → 记忆块（空结果/非法响应均有降级）
              └─► LLM  侦探 NPC   system prompt 注入记忆块 + 本局信息
                    └─► Code 提炼记忆   「玩家输入 + 侦探回应」→ 一条记录
                          └─► HTTP 写入记忆   POST {PC_URL}/v1/memory/remember
                                └─► Answer 回答   （玩家看到的内容）
```

7 个节点、6 条边，全部可从 Start 到达，**每条分支都终结于 Answer**。

> **为什么是单链**：首个提交版本曾把「写入记忆」放在一条与 Answer 并行的分支上，
> 那条分支末端没有 Answer 节点。Chatflow 要求每条分支都终结于 Answer，
> 这种悬空分支会被判为「DSL 配置存在阻断主要流程的错误」。
> 改成单链后记忆写入在回答之前完成，顺带保证了「回答出现时记忆一定已落库」。
> `scripts/check_dify_dsl.py` 现在会检查这条规则。

---

## 三、运行环境与版本

| 组件 | 版本 / 说明 |
|---|---|
| **Dify** | 建议 **1.0.0 及以上**（社区版，Docker Compose 部署）。DSL 的 `version` 字段为 `0.1.5`，可被 0.1.5 以上的 Dify 导入；0.7.0 以下的 DSL 版本在主流版本上均可正常导入 |
| **PowerContext** | 本仓库源码（分支 `2026-competition-ecosystem`），Python ≥ 3.11 |
| **模型服务** | 需要一个 LLM。DSL 默认使用 `langgenius/openai/openai` 的 `gpt-4o-mini`；**导入后可在界面改为你自己的模型**，不影响其余节点 |
| **Embedding / Rerank** | **不需要**。本应用不使用向量检索，也不挂知识库 |
| **插件** | **不需要任何市场插件**。`dependencies` 为空列表，不依赖 siliconflow 等第三方插件 |

### PowerContext 检索模式的重要说明

PowerContext 的**最小 Server 默认使用全文检索（lexical）**，不是向量检索。
因此召回依赖**词面重叠**：检索 query 里已经带上了案件编号、称呼和关键词，
所以同一个案件的记录能被稳定召回。

若评审环境希望使用语义检索，可把 PowerContext 的后端切到 seekdb
（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`）并配置向量搜索，本应用的请求格式不变。

---

## 四、完整运行步骤

### 步骤 1：启动 PowerContext

```bash
git clone https://github.com/Amonginin/powercontext-2026.git
cd powercontext-2026
git checkout 2026-competition-ecosystem

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# 启动 Server（默认 SQLite 持久化，无需额外数据库）
powercontext server run
```

服务默认监听 `http://127.0.0.1:8000`。

**确认就绪**（`/health/ready` 永远公开）：

```bash
curl -s http://127.0.0.1:8000/health/ready
```

> 本机 loopback 安装默认**不启用鉴权**，因此无需 token。
> 若你的实例启用了鉴权，所有请求都要加 `-H "Authorization: Bearer <token>"`，
> 并把 token 填进 Dify 的 `POWERCONTEXT_TOKEN` 环境变量。

### 步骤 2：初始化 scope，拿到 scope_id

```bash
cd dify/scripts
chmod +x init_scope.sh
POWERCONTEXT_URL=http://127.0.0.1:8000 ./init_scope.sh
```

脚本会打印：

```
SCOPE_ID=<scope id>
```

**这是幂等的**：重复执行复用同一个 scope（`idempotency_key` 固定），
所以它同时也是「恢复初始状态」的手段。

### 步骤 3：导入 DSL

1. 打开 Dify 控制台 → **工作室** → **导入 DSL 文件**
2. 选择 `dify/apps/memory-vault.yml`
3. 导入后进入应用编排界面，确认节点齐全（7 个节点）

### 步骤 4：填写环境变量

在应用的 **环境变量** 面板里改这三项：

| 变量名 | 填什么 |
|---|---|
| `POWERCONTEXT_URL` | PowerContext 的地址。**Dify 跑在 Docker 里时不能用 `localhost`** —— 见下方「网络条件」 |
| `POWERCONTEXT_SCOPE_ID` | 步骤 2 打印的 `SCOPE_ID` |
| `POWERCONTEXT_TOKEN` | 留空（未启用鉴权时）；启用鉴权时填 token |

### 步骤 5：发布并体验

1. 点 **发布** → 选择 **Web App** → 得到访问地址
2. 用 **API 访问** 页签拿到 API Key（若要用 API 调用）
3. 打开 Web App，按验证样例操作（见 `VERIFICATION.md`）

### 步骤 6（可选）：恢复初始状态

```bash
# 删除 scope 即可清空该作用域下的全部记忆
curl -X DELETE http://127.0.0.1:8000/v1/scopes/<scope_id>
cd dify/scripts && POWERCONTEXT_URL=... ./init_scope.sh   # 重新建一个干净的 scope
```

---

## 五、网络条件与资源需求

### Dify 访问 PowerContext 的地址

**这是最常见的失败点。** Dify 跑在 Docker 容器里时，容器内的 `localhost`
指的是容器自己，不是宿主机。

| 部署方式 | `POWERCONTEXT_URL` 应该填 |
|---|---|
| Dify 与 PowerContext **在同一台机器**，Dify 用 Docker Compose | `http://host.docker.internal:8000`（Mac/Windows 原生支持；Linux 需在 compose 里加 `extra_hosts: ["host.docker.internal:host-gateway"]`） |
| Dify 与 PowerContext **在同一 Docker 网络** | `http://<powercontext容器名>:8000`（需把 PowerContext 也加入同一网络） |
| PowerContext 在**另一台机器** | `http://<该机器IP>:8000`，并放行防火墙 8000 端口 |
| **Dify Cloud** | **必须**是公网可达的地址，不能是内网或 localhost |

### 其他条件

- PowerContext 运行时**不要**把 `8000` 暴露到公网；用反向代理 + TLS 时，
  把 `POWERCONTEXT_URL` 改成 `https://` 形式。
- 资源：PowerContext 最小实例 1 核 1G 足够；Dify 官方 compose 建议 4 核 8G。
- **服务就绪后再发起请求**：Dify 启动后等 `/health/ready` 返回成功再导入并发布应用，
  否则第一次召回会失败（虽然 DSL 有降级兜底，但评审会看到「初次合作」的文案）。

---

## 六、配置示例（占位值，不含真实凭据）

`scripts/powercontext.env.example`：

```bash
# PowerContext 服务地址
POWERCONTEXT_URL=http://host.docker.internal:8000

# 由 scripts/init_scope.sh 产出
POWERCONTEXT_SCOPE_ID=replace-with-scope-id

# 仅在实例启用鉴权时填写；默认 loopback 安装留空
POWERCONTEXT_TOKEN=replace-with-token
```

**本仓库不包含任何真实 API Key、密码或 token。**

---

## 七、已知功能限制

1. **召回是全文检索，不是语义检索。** 最小 Server 未配置向量搜索时，
   query 与记忆文本需要有词面重叠。本应用的 query 自带案件编号与称呼，
   所以同一案件能稳定召回；跨案件召回可能为空。
2. **记忆是按 scope 隔离的。** 不同 scope 之间不共享记忆，这是设计如此。
3. **记忆条目有长度上限**（单条 text 不超过 8192 字节），本应用写入前已截断到 300 字。
4. **NPC 的「记住」是软记忆**：它基于召回到的文本生成回答，
   不保证逐字复述玩家的原话——所以验证样例里我们会用**关键词**（称呼、排除的方向）
   而不是逐字引用来判定记忆是否生效。
5. **模型输出格式**：LLM 节点的回复长度受模型自身影响，应用不强制截断。
   若模型返回空，`Answer` 节点会输出空内容——建议在模型里保留足够的 temperature 与长度。

---

## 八、文件清单与导入顺序

| 顺序 | 文件 | 用途 | 题面对应 |
|---:|---|---|---|
| 1 | `apps/memory-vault.yml` | **Dify DSL**，导入这个文件 | 4.1 |
| 2 | `scripts/init_scope.sh` | 建 scope、产出 `scope_id` | 4.4「预置记忆或互动状态」 |
| 3 | `scripts/powercontext.env.example` | 环境变量占位样例 | 4.2「配置项」 |
| 4 | `scripts/check_dify_dsl.py` | 本地校验 DSL 结构（可选） | — |
| 5 | `scripts/simulate_dify_dsl.py` | 本地模拟执行工作流（可选） | — |
| 6 | `docs/RUNBOOK.md` | 本文件 | 4.2 |
| 7 | `docs/VERIFICATION.md` | 验证样例与运行证据 | 4.3 |
| 8 | `evidence/` | 截图与 PowerContext 读写记录 | 4.3 |

**导入顺序**：只有第 1 项是「导入 Dify」意义上的导入；
第 2 项必须**先于**第 1 项的发布执行（要先有 scope_id 才能填环境变量）。
