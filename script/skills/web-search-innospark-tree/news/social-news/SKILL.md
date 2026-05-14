# Social News — Layer 3 叶子节点

**脚本根目录**：`/Users/wxj/Documents/skills测试/news-aggregator-skill/`
⚡ **无需 API Key，开箱即用**

## 执行命令

```bash
cd /Users/wxj/Documents/skills测试/news-aggregator-skill

# 微博热搜
python3 scripts/fetch_news.py --source weibo --limit 10 --no-save

# V2EX 社区
python3 scripts/fetch_news.py --source v2ex --limit 10 --no-save

# 社区早报（预设配置）
python3 scripts/daily_briefing.py --profile social

# 组合社交新闻
python3 scripts/fetch_news.py --source weibo,v2ex,tencent --no-save
```

## 补充：国内平台实时内容（OpenCLI）

若需要更实时的社交内容，使用 platform-content 的 chinese-platforms 叶子节点：
```bash
opencli weibo hot --limit 10        # 微博实时热搜
opencli zhihu hot --limit 10        # 知乎热榜
opencli v2ex hot --limit 10         # V2EX 热帖
```

## 输出格式模板

```
#### N. [标题（中文翻译）](原始URL)
- **Source**: 来源 | **Time**: 时间 | **Heat**: 🔥 热度值
- **Summary**: 一句话社会事件摘要。
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install -r .../news-aggregator-skill/requirements.txt` |
| 微博数据为空 | 检查网络连接 |
