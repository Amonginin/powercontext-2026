# minimax — 生态赛题「MiniMax」资产目录

> 作品：**规有所依** —— 面向软件持续演进的 SDD 上下文管理系统。
> 提交时分支填 `2026-competition-ecosystem`，commit 填本目录最新提交的**完整 40 位 SHA**。

题面：https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qlqnoryzrs7gtqbw

**本题是全有全无：六项必做全过得 20 分，任一项不过得 0 分。**

## 一句话

给它一个开发任务，它从 **seekdb** 里查出适用的规格条款、要改的代码和验证用的测试，
拼成上下文交给 **MiniMax** 改代码；改前测试因功能未实现而失败、改后全过；
任务进展存进 **PowerContext**，重启后读回。

## 六项验收 → 本目录的证据

| # | 验收项 | 落地方式 | 证据文件 |
|---:|---|---|---|
| 1 | 能够运行 | CLI `python -m sdd <子命令>`，`scripts/run_all.sh` 一键跑 | `docs/RUNBOOK.md` |
| 2 | MiniMax 实际参与 | `sdd/agent.py` 调 OpenAI 兼容端点，要求输出 unified diff | `evidence/03-minimax-request.json` `03-minimax-response.json` `04-patch.diff` |
| 3 | 上下文正确 | 规格按条款入库，`sdd/retrieve.py` 从库里查；规格更新后条款整体替换 | `evidence/02-retrieve-context.json` `09-spec-update-*.json` |
| 4 | 数据库实际参与 | 规格/代码/测试存进 seekdb（MySQL 协议），上下文来源带 `content_digest` 可回查 | `evidence/01-ingest.log` `06-db-queries.log` |
| 5 | 状态能够复用 | 状态写 PowerContext + seekdb 两处，读优先 PowerContext | `evidence/07-powercontext-state.json` `08-restart-recovery.log` |
| 6 | 代码修改有效 | `sdd/verify.py` 按 pytest 退出码区分「功能未实现」与「环境错误」 | `evidence/05-test-before.log` `05-test-after.log` `05-test-regression.log` |

## 目录结构

```
minimax/
├── README.md                   本文
├── docs/
│   ├── RUNBOOK.md              运行说明（依赖、配置、初始化、入口、限制）
│   ├── VERIFICATION.md         验证样例（任务、规格、步骤、预期、重置）
│   └── TECH-NOTE.md            一页技术说明（结构、选择方式、分工）
├── sdd/                        上下文模块
│   ├── config.py               环境变量配置
│   ├── database.py             seekdb / OceanBase 存储层
│   ├── ingest.py               规格切条款 + 资料入库
│   ├── retrieve.py             任务 → 规格/代码/测试（带来源与摘要）
│   ├── agent.py                MiniMax 调用
│   ├── verify.py               pytest 结果分类
│   ├── state.py                任务状态存取
│   └── cli.py                  子命令
├── sample-project/             可运行业务工程
│   ├── spec/SPEC-001.md        规格（min_value 尚未实现）
│   ├── src/tinycalc/           calculator.py 待改 · filters.py 不动
│   ├── tests/                  目标测试 + 回归测试（改前改后不改）
│   ├── staged-updates/         改了规格的版本（演示规格更新用）
│   └── snapshots/              改前 / 改后代码快照
├── scripts/
│   ├── run_all.sh              一键跑完整链路
│   ├── setup_seekdb.sh         在 Linux 上起 seekdb 并建库
│   └── demo_spec_update.sh     演示「规格更新后输出跟着更新」
├── evidence/                   全部原始证据
└── tests/test_sdd_module.py    模块自身单测（27 项）
```

## 快速跑通

```bash
# 0) 起 seekdb 与 PowerContext
bash scripts/setup_seekdb.sh
powercontext server run --host 127.0.0.1 --port 8000 &

# 1) 建表 + 建 scope，把打印出的 scope id 填进环境变量
export POWERCONTEXT_URL=http://127.0.0.1:8000
python -m sdd init
export POWERCONTEXT_SCOPE_ID=scp_xxx

# 2) 一键跑（没有 MINIMAX_API_KEY 会自动跳过改代码那一步）
bash scripts/run_all.sh
```

## 设计上值得注意的三点

1. **改前的失败必须是断言失败。** `sdd/verify.py` 把 pytest 退出码 1 + AssertionError 判为
   `missing_feature`，其余（2/3/4/5、导入错误、语法错误）一律判为 `environment_error`。
   题面明确禁止用环境错误冒充改前的功能失败。
2. **规格更新会真的改答案。** 来源 id 是内容寻址的，条款按 `role:path:` 前缀整体替换。
   曾经因为按 `material_id` 删除，导致新旧两版条款同时命中同一个查询 —— 已修，见 TECH-NOTE。
3. **工程资料与任务状态分开放。** 前者进 seekdb（题面要求「存储、检索工程资料」），
   后者进 PowerContext（题面要求「保存任务进展并在后续运行中读取」）。
   两边各有一条独立的证据链，便于评审核对。
