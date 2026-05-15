# Tech News — Layer 3 叶子节点

**脚本根目录**：`${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}/`
⚡ **无需 API Key，开箱即用**

## 执行命令

```bash
cd ${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}

# Hacker News（最稳定，推荐验证环境时用）
python3 scripts/fetch_news.py --source hackernews --limit 15 --no-save

# GitHub Trending
python3 scripts/fetch_news.py --source github --limit 10 --no-save

# Product Hunt（今日发布）
python3 scripts/fetch_news.py --source producthunt --limit 10 --no-save

# V2EX
python3 scripts/fetch_news.py --source v2ex --limit 10 --no-save

# 组合获取
python3 scripts/fetch_news.py --source hackernews,github,producthunt --no-save

# 使用预设的技术早报配置
python3 scripts/daily_briefing.py --profile tech
```

## 关键词过滤

```bash
# 只看 AI 相关（自动扩展为 AI,LLM,GPT,Claude,Agent,RAG,DeepSeek）
python3 scripts/fetch_news.py --source hackernews --keyword "AI,LLM" --no-save
```

## 输出格式
抓取结果为 JSON，使用以下模板格式化展示：

```
#### N. [标题（中文翻译）](原始URL)
- **Source**: 来源 | **Time**: 时间 | **Heat**: 🔥 热度值
- **Summary**: 一句话摘要。
- **Deep Dive**: 💡 **Insight**: 背景、影响、技术价值。
```

## 前置依赖

```bash
pip install -r ${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}/requirements.txt
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install -r .../news-aggregator-skill/requirements.txt` |
| `No such file: scripts/fetch_news.py` | 检查路径是否正确 |
