# Daily Briefing — Layer 3 叶子节点

**执行脚本**：`/Users/wxj/Documents/skills测试/Intel_Briefing/run_mission.py`

## 功能
从 10+ 数据源自动抓取、翻译、分析，生成含8大板块的中文日报：
- 技术趋势 / 资本动向 / 学术前沿 / 产品精选 / 社区讨论 / X热文 / 深度洞察

## 执行命令

```bash
cd /Users/wxj/Documents/skills测试/Intel_Briefing && python run_mission.py
```

报告保存至 `Intel_Briefing/reports/` 目录。

## 环境要求

| 变量 | 说明 | 必须 |
|------|------|:----:|
| `GITHUB_TOKEN` | GitHub Trending API | ✅ |
| `XAI_API_KEY` | Grok（Twitter板块+AI分析） | 推荐 |
| `PRODUCTHUNT_TOKEN` | Product Hunt数据 | 可选 |
| `GEMINI_API_KEY` | 中文翻译 | 可选 |

```bash
# 检查必要变量
cat /Users/wxj/Documents/skills测试/Intel_Briefing/.env | grep GITHUB_TOKEN
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install -r /Users/wxj/Documents/skills测试/Intel_Briefing/requirements.txt` |
| `GITHUB_TOKEN not set` | 在 `.env` 中添加 `GITHUB_TOKEN=your_github_token_here` |
| `XAI_API_KEY not set` | Twitter板块跳过，其他板块不受影响 |
| 网络超时 | `export HTTP_PROXY=http://127.0.0.1:7890` |
