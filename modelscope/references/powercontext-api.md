# PowerContext 接入参考

本文件记录上下文召回助手依赖的 PowerContext 接口细节。SKILL.md 只写「怎么做」，
这里写「对接哪个字段」。

## 三种接入方式

### 1. HTTP API（最通用）

```
POST {base_url}/api/v1/context/search
Content-Type: application/json

{
  "query":        "用户偏好的报告风格",     // 语义检索语句
  "memory_kind":  "preference",             // preference | task | fact | conversation
  "budget":       5,                        // 最多返回条数
  "scope":        "session-2026-10-28"     // 可选：限定会话/项目/用户
}
```

返回条目通常包含：条目标识、类型、正文、创建/更新时间、来源。

### 2. MCP 工具

把 PowerContext 注册为 MCP Server，Agent 侧会看到一个上下文搜索工具，
参数与上面的 HTTP 请求体一一对应。

### 3. Python SDK

```python
from powercontext import ContextClient

client = ContextClient(base_url="http://127.0.0.1:8000")
hits = client.search_context(
    query="用户偏好的报告风格",
    memory_kind="preference",
    budget=5,
)
```

## 检索语义建议

`query` 是向量检索的输入，写法直接影响召回质量。

| 写法 | 召回效果 |
|---|---|
| 「用户偏好的报告风格」 | 好——上位概念，覆盖面广 |
| 「我之前说过我喜欢什么风格的报告」 | 差——口语化、与用户原话过于贴近 |

**用名词性的概念描述，不要把用户整句复述。**

## 任务状态（结构化）

任务类问题的状态是结构化存储的，比向量检索更准：

```
已完成事项 / 待办事项 / 已有验证结果 / 关联来源
```

问题涉及「做到哪一步了」时，**先读状态，再做语义检索**——
状态是权威的，检索只是补充上下文。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 检索总是返回空 | `query` 过于口语化 | 换成上位概念再试 |
| 检索返回不相关条目 | `memory_kind` 选错 | 换类别，或不指定类别 |
| 状态读不到 | 任务 ID 未确定 | 先用语义检索定位任务，再按 ID 读状态 |
| 重复返回同一批 | 没带 `scope` | 限定会话/项目范围 |

## 参考

- PowerContext 接口说明：<https://powercontext.oceanbase.io/zh/docs/develop/interfaces/>
- PowerContext 快速开始：<https://powercontext.oceanbase.io/zh/docs/develop/api-quickstart/>
