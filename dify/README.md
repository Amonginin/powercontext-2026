# dify — 生态赛题「Dify」资产目录

> 作品：**记忆银行（Memory Vault）** —— 跨会话记忆养成的侦探 NPC。
> 提交时分支填 `2026-competition-ecosystem`，commit 填本目录最新提交的**完整 40 位 SHA**。

题面：https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/lhfslihtnnb8yqh6

## 一句话

NPC 把你说过的话、排除过的线索、偏好的称呼存进 **PowerContext 记忆库**；
**下一次开新会话，它依然认得你**。

## 文件清单与导入顺序

| 顺序 | 文件 | 用途 | 题面对应 |
|---:|---|---|---|
| 1 | `apps/memory-vault.yml` | **Dify DSL** —— 在 Dify 的「导入 DSL 文件」里导入这个 | 4.1 |
| 2 | `scripts/init_scope.sh` | 建 scope 并打印 `scope_id`（幂等，也是恢复初始状态的手段） | 4.4 |
| 3 | `scripts/powercontext.env.example` | 环境变量占位样例 | 4.2 |
| 4 | `scripts/check_dify_dsl.py` | 本地校验 DSL 结构（可选） | — |
| 5 | `scripts/simulate_dify_dsl.py` | 本地模拟执行工作流（可选） | — |
| 6 | `docs/RUNBOOK.md` | **运行说明** | 4.2 |
| 7 | `docs/VERIFICATION.md` | **验证样例与运行证据** | 4.3 |
| 8 | `evidence/` | 截图、记忆读写记录、节点执行记录 | 4.3 |

**执行顺序**：先跑 `scripts/init_scope.sh` 拿到 `scope_id` → 再导入 DSL →
在 Dify 环境变量里填 `POWERCONTEXT_URL` / `POWERCONTEXT_SCOPE_ID` / `POWERCONTEXT_TOKEN` →
发布 → 按 `docs/VERIFICATION.md` 的样例操作。

**注意**：只有第 1 项是「导入 Dify」意义上的导入。第 2 项必须先于发布执行。

## 技术分工

- **Dify（必选）**：核心流程编排 —— Start 收输入 → HTTP 召回记忆 → Code 解析成提示词块 →
  LLM 生成角色回复 → Code 提炼记忆 → HTTP 写回记忆库 → Answer 输出。
  单链结构，每条分支都终结于 Answer（Chatflow 的硬性要求）。
- **PowerContext**：`POST /v1/memory/search` 召回、`POST /v1/memory/remember` 写入。
  召回结果**直接注入 LLM 的 system prompt**，写入的是**玩家本轮真实输入**。

按题面「选择 PowerContext 的作品不额外要求使用 seekdb」，本作品只用 PowerContext
一种指定技术即满足门槛。PowerContext 默认 SQLite 持久化，符合 FAQ「PowerContext 自身
使用 SQLite 持久化符合要求」。

## 本地校验

```bash
pip install pyyaml
python3 dify/scripts/check_dify_dsl.py dify/apps/memory-vault.yml
python3 dify/scripts/simulate_dify_dsl.py dify/apps/memory-vault.yml
```

`check_dify_dsl.py` 会检查结构，包括**每条分支是否终结于 Answer** —— 首个提交版本
就是因为「写入记忆」那条并行分支悬空被判「DSL 配置存在阻断主要流程的错误」。
`simulate_dify_dsl.py` 会真的执行 DSL 里两个 code 节点的 Python，并验证记忆读写请求体的字段。
