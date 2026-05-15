# Finance News — Layer 3 叶子节点

**脚本根目录**：`${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}/`
⚡ **无需 API Key，开箱即用**

## 执行命令

```bash
cd ${WEB_SEARCH_NEWS_AGGREGATOR_ROOT:-../news-aggregator-skill}

# 华尔街见闻（中文财经资讯）
python3 scripts/fetch_news.py --source wallstreetcn --limit 10 --no-save

# 36氪（科技+商业）
python3 scripts/fetch_news.py --source 36kr --limit 10 --no-save

# 腾讯财经
python3 scripts/fetch_news.py --source tencent --limit 10 --no-save

# 组合财经早报
python3 scripts/fetch_news.py --source wallstreetcn,36kr,tencent --no-save

# 使用预设配置
python3 scripts/daily_briefing.py --profile finance
```

## 补充：实时行情数据（OpenCLI）

若需要实时股票价格，使用 platform-content 的 finance-markets 叶子节点：
```bash
opencli xueqiu hot-stock --limit 10
opencli sinafinance news --limit 10 --type 1
```

## 输出格式模板

```
#### N. [标题（中文翻译）](原始URL)
- **Source**: 来源 | **Time**: 时间
- **Summary**: 一句话财经摘要。
- **Deep Dive**: 💡 **Insight**: 市场影响、投资逻辑、宏观背景。
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install -r .../news-aggregator-skill/requirements.txt` |
| 数据为空 | 检查网络，华尔街见闻需要稳定网络 |
