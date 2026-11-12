# 一页技术说明 · 规有所依

> Dify 赛题提交材料「一页技术说明」：模块结构、上下文选择方式、各项技术的分工。

## 模块结构

```
sample-project/            可运行业务工程（有测试）
  spec/SPEC-001.md         规格：要求 apply(min_value=N)，实现里刻意没有
  src/tinycalc/            calculator.py（待改）· filters.py（不动）
  tests/                   test_calculator.py（目标测试）· test_filters.py（回归）
  staged-updates/          改了规格的版本，用于演示「规格更新后输出跟着更新」
  snapshots/               改前 / 改后代码快照

sdd/                       上下文模块本体（CLI：python -m sdd）
  config.py                从环境变量读配置
  database.py              seekdb / OceanBase 存储层（MySQL 协议）
  ingest.py                规格 → 芯片级条款；代码 / 测试 → 按文件入库
  retrieve.py              任务 → 规格条款 + 代码 + 测试（每条带来源与摘要）
  agent.py                 MiniMax 调用，要求输出 unified diff
  verify.py                跑 pytest，区分「功能未实现」与「环境错误」
  state.py                 任务状态写入 PowerContext，重启后读回
  cli.py                   子命令编排
```

## 上下文选择方式

任务描述先过一层关键词提取：英文标识符按 `_` 拆开（`min_value` → `min_value`、`min`、`value`），
中文按连续汉字切块，停用词（「的」「实现」「增加」等）丢弃。

然后**在数据库里检索**，不是扫本地文件：

| 来源 | 查询 | 排序 |
|---|---|---|
| 规格条款 | `sdd_clause` 按 `body LIKE` / `title LIKE` | 标题命中权重 3、正文命中权重 1，再按条款长度 |
| 代码 | `sdd_material WHERE role='code'` 按内容或路径匹配 | 路径序 |
| 测试 | 同上，`role='test'`，并抽出去重后的 `def test_*` 函数名 | 路径序 |

每条来源都带 `material_id` / `clause_id` + `content_digest`。摘要是内容寻址的
（`sha256` 前 12 位），所以：

- **来源可核对**：拿 `material_id` 去库里读回，摘要必须一致；
- **规格更新会改答案**：规格改了 → 摘要变了 → 条款按 `role:path:` 前缀整体替换 →
  上下文引用新条款。旧条款被删除，不会和新条款同时出现（这一点有过一次真实 bug，见下）。

**踩过的坑**：最初 `replace_clauses` 按 `material_id` 删除。但 `material_id` 是内容寻址的，
规格一改就换了 id，旧条款成了孤儿，结果同一份规格的新旧两版条款同时被检索出来。
改成按 `role:path:` 前缀删除后修复。

## 三项技术的分工

| 技术 | 承担什么 | 在代码里的位置 | 怎么核对 |
|---|---|---|---|
| **MiniMax** | 生成实现 `min_value` 的 unified diff | `agent.py` | `evidence/03-minimax-request.json`（含 prompt 与上下文来源列表）+ `03-minimax-response.json`（含 `request_id`、`model`、`usage`）+ `04-patch.diff`，与仓库里的代码差异逐字对应 |
| **seekdb / OceanBase** | 存规格、代码、测试与规格条款；上下文里的每条来源都来自对它的查询 | `database.py` | `evidence/06-db-queries.log` 是真实 SQL 与输出；上下文里每条来源的 `content_digest` 都能在 `sdd_material` 里查到 |
| **PowerContext** | 存任务进展与验证结果，重启后读回并注入上下文 | `state.py` | `evidence/07-powercontext-state.json` + `08-restart-recovery.log`（重启前后进程不同、状态一致） |

## 为什么这样分工

- **MiniMax 只做「改代码」这一件事**。上下文构建、测试判定、状态管理都不依赖模型，
  所以模型不可用时整条链路仍能跑到「拿到上下文」为止，评审能独立核对每一项。
- **工程资料放 seekdb，不放 PowerContext**。题面对 seekdb/OceanBase 的要求是
  「保存工程资料或索引，查询结果用于构造任务上下文」，而 PowerContext 的定位是
  「保存任务进展」。两者职责分开，各自都有独立的证据链。
- **状态写两处、读优先 PowerContext**。写两处是为了评审能直接查库核对；
  读优先 PowerContext 是因为题面要求证明「状态确实交给了 PowerContext」。

## 判定「功能未实现」而不是「环境坏了」

题面明确禁止用环境错误冒充改前的失败。`verify.py` 按 pytest 退出码分类：

| 退出码 | 含义 | 归类 |
|---:|---|---|
| 0 | 全过 | passed |
| 1 + 断言失败 | 测试成功执行、断言不成立 | **missing_feature**（唯一合格的改前形态） |
| 1 + 导入/语法错误 | 环境问题 | environment_error |
| 2 / 3 / 4 | 中断 / 内部错误 / 用法错误 | environment_error |
| 5 | 一个测试都没收集到 | environment_error |

改前那次：`collected: 18  passed: 14  failed: 4  exit_code: 1  category: missing_feature`，
四条失败全部是 `AssertionError` / `DID NOT RAISE ValueError`。
