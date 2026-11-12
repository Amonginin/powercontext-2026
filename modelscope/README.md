# ob-context-recall — 上下文召回助手

基于 OceanBase PowerContext 的 Skill，用于在长对话 / 多次会话中找回之前的信息：
用户偏好、任务进展、历史结论、已验证结果。

## 目录结构

```
ob-context-recall/
├── SKILL.md                      # 主文件（必需）
└── references/
    └── powercontext-api.md       # PowerContext 接口字段与接入方式
```

## 安装

```bash
# 魔搭 CLI
pip install --upgrade modelscope
modelscope skills add @<owner>/ob-context-recall

# 或通用 Agent Skills 安装器
npx skills add https://modelscope.cn/skills/@<owner>/ob-context-recall
```

## 使用示例

```
我之前说过我偏好什么样的报告格式？
```

```
继续上次那个 PDF 证据检索的任务，做到哪一步了？
```

```
帮我总结一下我们讨论过的数据库索引优化方案。
```

## 设计要点

1. **先核验再回答**：所有事实性内容必须来自检索结果或状态记录，不得推测。
2. **来源可追溯**：回答中标注条目标识或更新时间。
3. **缺口如实说明**：检索内容不足以回答时说缺口，不用通用知识伪装成历史信息。
4. **状态与检索并用**：任务类问题既读结构化状态，也做语义检索。
