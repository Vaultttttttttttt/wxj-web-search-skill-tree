# Multi-Platform Search — Layer 2 路由（多平台批量搜索）

## 🧭 你在哪里
`web-search-innospark-tree/multi-platform-search/`
工具来源：Union Search (`${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/`)

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| GitHub代码/仓库、StackOverflow、DuckDuckGo、Bing、Wikipedia技术搜索 | `code-dev` | `./code-dev/SKILL.md` |
| Reddit、Twitter、YouTube、小红书、B站、跨社交平台搜索 | `social` | `./social/SKILL.md` |
| 批量图片下载、多平台图片搜索（Pixabay/Unsplash等18平台） | `image-search` | `./image-search/SKILL.md` |

---

## 🔀 路由决策

```
GitHub/代码/技术文档/DuckDuckGo   → code-dev（无需API Key，直接可用）
社交平台内容/舆情                  → social
图片素材/批量下载                  → image-search（18平台，无需API Key）
同时搜多个技术+社交平台             → 并行执行 code-dev + social
```

---

## ⚠️ 前置检查

```bash
# 最基础依赖（code-dev 和 image-search 都需要）
pip install requests python-dotenv duckduckgo-search

# 验证
cd ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}
python scripts/duckduckgo/duckduckgo_search.py "test" --limit 1
```

---

## 📁 子任务目录

```
multi-platform-search/
├── SKILL.md           ← 你在这里（Layer 2）
├── code-dev/          → GitHub/技术类搜索（无需API）
├── social/            → 社交媒体搜索
└── image-search/      → 多平台图片批量搜索
```
