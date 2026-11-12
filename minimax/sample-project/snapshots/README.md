# 代码快照

`before/` 与 `after/` 各存一份改动前后的文件副本，用于逐字比对「改了什么」。

- `before/` 是初始状态：`calculator.py` 没有 `min_value` 支持。
- `after/` 在应用 MiniMax 的补丁后填充。

`test_calculator.py` 和 `test_filters.py` 在两侧**必须完全一致** ——
题面要求「测试集在修改前后保持一致，不得通过删除断言、跳过测试或修改预期结果使其通过」。
用下面的命令可以证明：

```bash
diff -r snapshots/before snapshots/after --exclude=README.md
```

预期只列出 `calculator.py` 有差异。
