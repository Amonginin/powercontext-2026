# gaode — 生态赛题资产目录

本目录存放「高德地图」赛题的参赛资产。评测平台按目录名定位对应赛题，
提交时分支填 `2026-competition-ecosystem`，commit 填本分支最新提交。

## 提交产物

**只有一个文件：`SKILL.md`**（题面「提交产物：一个 Skill 文件（SKILL.md）」）。

评测方加载本目录下的 Skill，在统一提供的 Agent 环境里执行单轮任务，
用「高德数据校验 + AI 自动评分」打分，满分 20 分。

## Skill 做了什么

引导 Agent 使用高德地图 MCP Server（`amap-maps`）的两个能力：

1. `maps_around_search` —— 周边地点搜索，拿到候选 POI；
2. `maps_direction_walking` —— 步行路径规划，拿到候选点的**真实步行距离与耗时**。

再按「步行时间上限 / 推荐数量 / 偏好」筛选排序，输出最多 3 个带
名称、地址、步行距离、步行时间、推荐理由的推荐。

## 关键设计取舍

- **步行时间只能来自 `maps_direction_walking`**。评测平台会自动采集工具调用记录，
  核对地点与路线；用直线距离替代属于数据造假，直接失分。
- **坐标格式固定 `经度,纬度`**（高德 GCJ02，lng 在前），直接用工具返回的 `location`。
- **候选要多取**：`maps_around_search` 单页默认 10 条、上限 25 条，
  Skill 明确要求设 `page_size=25` 并在不足时翻页/扩半径，
  否则凑不满 3 个合格推荐。
- **不编造**：安静程度、排队、营业状态等工具没返回的一律标「未核实」；
  结果不足或查询失败时如实说明并给出放宽条件的建议。

## 校验

```bash
python3 scripts/check_skill.py gaode/SKILL.md
```

## 题面

https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qp60miwsbk1mcgod
