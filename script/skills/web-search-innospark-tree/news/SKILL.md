# News — Layer 2 路由（新闻聚合）

## 🧭 你在哪里
`web-search-innospark-tree/news/`
工具来源：News Aggregator (`/Users/wxj/Documents/skills测试/news-aggregator-skill/`)

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| GitHub Trending、Hacker News、Product Hunt、V2EX、DEV.to | `tech-news` | `./tech-news/SKILL.md` |
| HuggingFace、AI Newsletter、Ben's Bites、AI论文 | `ai-news` | `./ai-news/SKILL.md` |
| 华尔街见闻、36氪、腾讯财经、经济类新闻 | `finance-news` | `./finance-news/SKILL.md` |
| 微博热搜、社区动态、V2EX、国内社交 | `social-news` | `./social-news/SKILL.md` |

---

## 🔀 路由决策

```
"技术/开发/HN/GitHub"    → tech-news
"AI/LLM/模型/论文"       → ai-news
"财经/股票/市场/经济"    → finance-news
"社交/热搜/微博/舆情"    → social-news
"全部/综合早报"          → 并行执行所有4个子任务
```

---

## ⚠️ 前置检查

```bash
# 安装依赖
pip install -r /Users/wxj/Documents/skills测试/news-aggregator-skill/requirements.txt

# 验证基础功能（HN，无需任何Key）
cd /Users/wxj/Documents/skills测试/news-aggregator-skill
python3 scripts/fetch_news.py --source hackernews --limit 3 --no-save
```

**注意**：
- `ai-news` 中的 HuggingFace 和 Ben's Bites 需要 Playwright
- 其他所有来源**无需 API Key**，开箱即用

---

## 📁 子任务目录

```
news/
├── SKILL.md           ← 你在这里（Layer 2）
├── tech-news/         → HN/GitHub/Product Hunt等技术新闻
├── ai-news/           → AI/ML专项新闻和论文
├── finance-news/      → 财经类新闻
└── social-news/       → 微博/V2EX等社交新闻
```
