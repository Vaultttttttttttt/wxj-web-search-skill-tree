# Social Stream — Layer 3 叶子节点

**工具**：`bird`（Twitter/X）+ `feedparser`（RSS）

## Twitter / X（需要 bird CLI）

### 安装
```bash
npm install -g @steipete/bird
```

### 命令

```bash
# 读取单条推文或线程
bird read "https://x.com/user/status/xxx"

# 搜索推文
bird search "AI agent 2026"

# 查看时间线
bird timeline

# 用户主页
bird profile elonmusk
```

### Cookie 配置（首次使用）
```bash
agent-reach configure twitter-cookies "你的cookie_json"
```

## RSS 订阅

### 安装
```bash
pip install feedparser
```

### 读取 RSS/Atom

```bash
# 命令行方式
python3 -c "
import feedparser
f = feedparser.parse('https://hnrss.org/frontpage')
for e in f.entries[:10]:
    print(e.title, '-', e.link)
"

# 常用 RSS 源
# Hacker News:    https://hnrss.org/frontpage
# GitHub Trending: https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml
# Reddit:         https://www.reddit.com/r/MachineLearning/.rss
```

### Union Search RSS 脚本

```bash
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/rss_search/rss_search.py \
  --url "https://hnrss.org/frontpage"
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `command not found: bird` | `npm install -g @steipete/bird` |
| Twitter `AuthorizationError` | `agent-reach configure twitter-cookies "..."` |
| `ModuleNotFoundError: feedparser` | `pip install feedparser` |
