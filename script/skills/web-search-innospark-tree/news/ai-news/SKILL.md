# AI News — Layer 3 叶子节点

**脚本根目录**：`${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}/`

## 执行命令

```bash
cd ${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}

# HuggingFace 每日论文（⚠️ 需要 Playwright）
python3 scripts/fetch_news.py --source huggingface --deep --no-save

# 全部 AI Newsletter（汇总多个AI订阅）
python3 scripts/fetch_news.py --source ai_newsletters --limit 20 --no-save

# Ben's Bites（⚠️ 需要 Playwright）
python3 scripts/fetch_news.py --source bensbites --limit 10 --no-save

# Interconnects（AI研究视角）
python3 scripts/fetch_news.py --source interconnects --limit 5 --no-save

# KDnuggets（ML/数据科学）
python3 scripts/fetch_news.py --source kdnuggets --limit 10 --no-save

# ChinAI（中国AI视角）
python3 scripts/fetch_news.py --source chinai --limit 5 --no-save

# AI深度日报（预设配置）
python3 scripts/daily_briefing.py --profile ai_daily

# 组合（不需要Playwright的稳定来源）
python3 scripts/fetch_news.py --source ai_newsletters,interconnects,kdnuggets --no-save
```

## ⚠️ Playwright 依赖

HuggingFace 和 Ben's Bites 需要 Playwright：
```bash
pip install playwright && playwright install chromium
```
其他 AI 来源**无需 Playwright**。

## 输出格式模板

```
#### N. [标题（中文翻译）](原始URL)
- **Source**: 来源 | **Time**: 时间
- **Summary**: 论文/文章核心内容一句话。
- **Deep Dive**: 💡 **Insight**: 技术贡献、应用价值、行业影响。
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError: playwright` | `pip install playwright && playwright install chromium` |
| HuggingFace 超时 | 改用 `--source ai_newsletters`（不需要Playwright） |
