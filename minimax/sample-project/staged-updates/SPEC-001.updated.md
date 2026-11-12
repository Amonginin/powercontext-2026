# SPEC-001 TinyCalc 筛选条件

> 状态：生效中（**修订版** — 在原规格基础上新增第 2.3 节）
> 适用范围：`src/tinycalc/calculator.py` 的 `Calculator.apply`
> 修订说明：本次修订**只新增约束**，不改变第 2.1 / 2.2 节的任何既有语义。

## 1. 范围

`Calculator.apply` 负责按传入的筛选条件过滤一组数值。
本规格定义 `min_value` 筛选条件，并明确既有 `max_value` 筛选条件不得改变。

## 2. 需求

### 2.1 min_value 筛选（本任务目标）

- `apply(min_value=N)` 必须只返回**大于等于** `N` 的元素。
- `min_value` 必须是**非负整数**；否则必须抛出 `ValueError`。
- `min_value` 与 `max_value` 可以同时出现，两者都必须生效。

### 2.2 既有行为（必须保持）

- `apply(max_value=N)` 语义不变：只返回**小于等于** `N` 的元素。
- `apply()` 不带任何条件时，返回原列表的**副本**（不是同一个对象）。
- 结果保持输入顺序。
- `total(**filters)` 等价于 `sum(apply(**filters))`。

### 2.3 min_value 的取值上限（**本次新增**）

- `min_value` 的取值范围为 `0` 到 `1000`（含两端）。
- `min_value > 1000` 时必须抛出 `ValueError`。
- `min_value < 0` 时必须抛出 `ValueError`（沿用第 2.1 节的非负约束）。

> 新增本节后，第 2.1 节的「非负整数」约束具体化为「`0 <= N <= 1000` 的整数」。

## 3. 验收

| 类型 | 位置 |
|---|---|
| 目标测试 | `tests/test_calculator.py` — `TestMinValueFilter` 类 |
| 既有行为测试 | `tests/test_calculator.py` — `TestExistingBehaviour` 类 |
| 回归测试 | `tests/test_filters.py` |

目标测试在功能实现前必须以**断言失败**（而非环境错误）失败；
实现后必须全部通过，回归测试必须保持通过。

## 4. 非目标

- 不引入新的依赖。
- 不改动 `src/tinycalc/filters.py`。
- 不改变 `Calculator` 的构造签名。
