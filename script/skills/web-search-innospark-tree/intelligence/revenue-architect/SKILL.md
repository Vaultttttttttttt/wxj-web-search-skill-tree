# Revenue Architect — Layer 3 叶子节点

**执行脚本**：`/Users/wxj/Documents/skills测试/Intel_Briefing/run_revenue_architect.py`

## 功能
读取当日简报内容，由 AI 从中提炼 5 类变现方向：
SaaS/工具、内容/社区、服务/咨询、开源商业化、数据/API。

## 执行命令

```bash
# 先生成日报（若当天尚未运行）
cd /Users/wxj/Documents/skills测试/Intel_Briefing && python run_mission.py

# 再运行变现分析
cd /Users/wxj/Documents/skills测试/Intel_Briefing && python run_revenue_architect.py
```

## 依赖关系
Revenue Architect 依赖当日简报的输出，建议先运行 `daily-briefing` 再执行本节点。
若报告已存在于 `reports/` 目录，可直接运行。

## 环境要求

| 变量 | 必须 |
|------|:----:|
| `GITHUB_TOKEN` | ✅ |
| `XAI_API_KEY` | ✅（变现分析核心） |

## 错误处理

| 错误 | 解决 |
|------|------|
| `No report found` | 先运行 `python run_mission.py` 生成日报 |
| `XAI_API_KEY not set` | 分析质量大幅降低，建议配置 |
