# 验证样例 · 规有所依

> MiniMax 赛题提交材料「验证样例」：任务描述、有效规格、初始数据、操作步骤、
> 预期结果及重置方法。并列出上下文中必须包含的规格、代码和测试来源，便于评审核对。

---

## 一、任务描述

> **为 TinyCalc 增加 `min_value` 筛选条件。**

`sample-project` 是一个可运行、有测试的小工程。`Calculator.apply` 目前只支持
`max_value`；`SPEC-001` 第 2.1 节要求它同时支持 `min_value`，而实现里刻意没有。

这是一个**局部修改**：只动 `calculator.py` 的 `apply` 方法，不改测试、不改 `filters.py`、
不加依赖。

---

## 二、有效规格

完整规格在 `sample-project/spec/SPEC-001.md`。本次任务适用的条款：

### 必须遵守（第 2.1 节）

- `apply(min_value=N)` 必须只返回**大于等于** `N` 的元素。
- `min_value` 必须是**非负整数**；否则必须抛出 `ValueError`。
- `min_value` 与 `max_value` 可以同时出现，两者都必须生效。

### 必须保持（第 2.2 节）

- `apply(max_value=N)` 语义不变：只返回小于等于 `N` 的元素。
- `apply()` 不带条件时返回原列表的**副本**（不是同一个对象）。
- 结果保持输入顺序。
- `total(**filters)` 等价于 `sum(apply(**filters))`。

### 非目标（第 4 节）

- 不引入新依赖；不改动 `src/tinycalc/filters.py`；不改 `Calculator` 构造签名。

---

## 三、上下文中必须包含的来源

评审可用 `evidence/02-retrieve-context.json` 逐条核对。构建上下文时，以下来源**必须**出现：

| 类型 | 必须包含 | 怎么核对 |
|---|---|---|
| 规格 | `SPEC-001` 的 `2.1 min_value 筛选（本任务目标）` | 该条 `identifier` 形如 `clause:SPEC-001:...`，`path` 为 `spec/SPEC-001.md` |
| 规格 | `SPEC-001` 的 `2.2 既有行为（必须保持）` | 同上，用于证明「哪些原有行为必须保持」 |
| 代码 | `src/tinycalc/calculator.py` | `identifier` 形如 `code:src/tinycalc/calculator.py:...` |
| 测试 | `tests/test_calculator.py`，且 `test_names` 里含 `test_min_value_*` | `identifier` 形如 `test:tests/test_calculator.py:...` |
| 测试 | `tests/test_filters.py`（回归） | 同上 |
| 状态 | 已完成 / 待办 / 已有验证结果 | `context.state` 字段（来自 PowerContext） |

每条来源都带 `content_digest`（`sha256:...`），可在 `sdd_material` 表按 `material_id` 查回并比对。

---

## 四、初始数据与初始状态

| 项 | 初始值 |
|---|---|
| seekdb 数据库 | `sdd_context`，三张空表 `sdd_material` / `sdd_clause` / `sdd_task_state` |
| PowerContext | 一个新建 scope，无任何记忆条目 |
| 样例工程 | `spec/SPEC-001.md` 是**未修订版**（只有 2.1/2.2，没有 2.3） |
| `calculator.py` | **没有** `min_value` 支持 |
| 测试文件 | 目标测试与回归测试都已在位，且改前改后**完全一致**（不得修改） |

初始化：

```bash
# 1) 空库
obclient -h127.0.0.1 -P2881 -uroot --protocol=tcp \
  -e "DROP DATABASE IF EXISTS sdd_context; CREATE DATABASE sdd_context CHARACTER SET utf8mb4;"

# 2) 空 scope
cd minimax && python -m sdd init

# 3) 确认记忆库为空
curl -s -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d "{\"scope_id\": \"$POWERCONTEXT_SCOPE_ID\", \"query\": \"min_value\"}"
# 期望 {"hits": []}
```

---

## 五、操作步骤

### 步骤 1：导入工程资料

```bash
python -m sdd ingest
```

**预期**：6 份资料入库（1 规格 + 3 代码 + 2 测试），规格解析出 **5 个条款**。
实际输出（`evidence/01-ingest.log`）：

```
spec:spec/SPEC-001.md:de950e7eee52 | spec | spec/SPEC-001.md | sha256:de950e7e... | 5
code:src/tinycalc/calculator.py:6edc50fa80a1 | code | src/tinycalc/calculator.py | ... | 0
test:tests/test_calculator.py:2344546f69b2 | test | tests/test_calculator.py | ... | 0
... 共导入 6 份资料
```

### 步骤 2：构建开发上下文

```bash
python -m sdd context --task "为 TinyCalc 增加 min_value 筛选条件" --evidence 02-retrieve-context.json
```

**预期**：`specs` 第一条是 `2.1 min_value 筛选（本任务目标）`；
`code` 含 `src/tinycalc/calculator.py`；`tests` 含两个测试文件且
`test_calculator.py` 的 `test_names` 里有四个 `test_min_value_*`。

### 步骤 3：确认改前测试确实「因功能未实现而失败」

```bash
python -m sdd verify --phase before
```

**预期**（`evidence/05-test-before.log`）：

```
command: python -m pytest tests -v --no-header -p no:cacheprovider
exit_code: 1
collected: 18
passed: 14
failed: 4
errors: 0
category: missing_feature
detail: 4 个断言失败，目标行为尚未实现
```

四条失败的形态必须是 `AssertionError` 或 `Failed: DID NOT RAISE ValueError`，
**不能**是 `ModuleNotFoundError` / `SyntaxError` / collection error。

### 步骤 4：MiniMax 改代码

```bash
python -m sdd agent --task "为 TinyCalc 增加 min_value 筛选条件"
python -m sdd apply
```

**预期**：
- `evidence/03-minimax-request.json`：模型、`reasoning_effort`、上下文来源列表（key 脱敏）
- `evidence/03-minimax-response.json`：`request_id`、`model`、`usage`、`content`、`reasoning_content`
- `evidence/04-patch.diff`：只改 `src/tinycalc/calculator.py`
- 补丁通过 `git apply --check` 后干净应用

### 步骤 5：改后测试与回归测试

```bash
python -m sdd verify --phase after
python -m sdd verify --phase regression -- tests/test_filters.py
```

**预期**：两条命令的 `exit_code: 0`，目标测试与回归测试全过。

### 步骤 6：验证「规格更新后输出跟着更新」

```bash
bash scripts/demo_spec_update.sh python
```

**预期**（`evidence/09-spec-update-{before,after}.json`）：更新后上下文中出现
新的 `2.3 min_value 的取值上限（本次新增）` 条款，规格摘要从
`sha256:de950e7e...` 变为 `sha256:b57792f6...`。

这一步专门用来证明「模块使用更新后的内容，不能始终返回固定答案」。

### 步骤 7：保存状态 → 重启 → 读回

```bash
python -m sdd state --task "为 TinyCalc 增加 min_value 筛选条件" --save \
  --done "导入规格、代码与测试资料" --todo "生成补丁并应用" \
  --verification "改前目标测试因功能未实现而失败"

# 停掉 PowerContext 进程，重新启动
python -m sdd state --task "为 TinyCalc 增加 min_value 筛选条件" --show
```

**预期**（`evidence/08-restart-recovery.log`）：重启后 `done` / `todo` / `verification`
与写入时一致，且 `extra.loaded_from == "powercontext.memory"` —— 说明状态是从
PowerContext 读回来的，不是进程内存或本地文件。

---

## 六、重置方法

```bash
# 1) 代码回到改前
cd sample-project
cp snapshots/before/calculator.py src/tinycalc/calculator.py
# 或： git checkout -- src/tinycalc/calculator.py

# 2) 规格回到未修订版
cp ../evidence/spec-before.md spec/SPEC-001.md

# 3) 清库重建
obclient -h127.0.0.1 -P2881 -uroot --protocol=tcp \
  -e "DROP DATABASE IF EXISTS sdd_context; CREATE DATABASE sdd_context CHARACTER SET utf8mb4;"
cd .. && python -m sdd init && python -m sdd ingest

# 4) 换一个新 scope（避免旧记忆干扰）
SCOPE_KEY=sdd-context-fresh python -m sdd init
```

---

## 七、证据索引

| 文件 | 对应验收项 | 内容 |
|---|---|---|
| `evidence/01-ingest.log` | 数据库实际参与 | 6 份资料的 `material_id` / `content_digest` / 条款数 |
| `evidence/02-retrieve-context.json` | 上下文正确 | 完整的上下文 JSON（specs / code / tests / state） |
| `evidence/06-db-queries.log` | 数据库实际参与 | 真实 SQL 与输出（含 `sdd_material` / `sdd_clause` / `sdd_task_state`） |
| `evidence/05-test-before.log` | 代码修改有效 | 改前：18 收集 / 4 断言失败 / `category: missing_feature` |
| `evidence/03-minimax-*.json` | MiniMax 实际参与 | 请求与响应原文（key 脱敏，含 `request_id`） |
| `evidence/04-patch.diff` | MiniMax 实际参与 | 与仓库代码差异逐字对应 |
| `evidence/05-test-after.log` | 代码修改有效 | 改后：目标测试全过 |
| `evidence/05-test-regression.log` | 代码修改有效 | 回归测试全过 |
| `evidence/07-powercontext-state.json` | 状态能够复用 | 写入 PowerContext 的状态原文 |
| `evidence/08-restart-recovery.log` | 状态能够复用 | 重启前后进程与读回结果 |
| `evidence/09-spec-update-*.json` | 上下文正确 | 规格更新前后的上下文对比 |

---

## 八、环境健康检查（证明改前的失败不是环境问题）

在跑 `verify --phase before` 之前，先确认环境本身是好的：

```bash
cd sample-project
python -c "import tinycalc; print(tinycalc.Calculator)"   # 能导入
pytest --collect-only -q                                   # 能收集
```

实际输出：

```
import OK: <class 'tinycalc.calculator.Calculator'>
18 tests collected in 0.02s
```

**18 个测试全部被收集**，说明失败不是「收集不到测试」；
四条失败是断言层面的（`assert [1, 5, 9] == [5, 9]` 和 `DID NOT RAISE ValueError`），
说明功能确实未实现，而不是依赖缺失或无法启动。
