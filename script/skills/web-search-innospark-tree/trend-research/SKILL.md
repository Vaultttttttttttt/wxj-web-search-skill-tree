# Trend Research — Layer 2 路由（近30天话题研究）

## 🧭 你在哪里
`web-search-innospark-tree/trend-research/`
工具来源：last30days (`/Users/wxj/Documents/skills测试/last30days-skill/`)

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| 快速了解某话题近期热度，时间有限 | `quick-mode` | `./quick-mode/SKILL.md` |
| 深度研究话题，需要完整社交媒体分析 | `deep-mode` | `./deep-mode/SKILL.md` |

---

## 🔀 路由决策

```
"快速/大概了解/简单看看"   → quick-mode（约1-2分钟）
"深度研究/完整报告/详细"   → deep-mode（约5分钟）
未指定时                   → quick-mode（默认，省时）
```

**数据来源**：Reddit, X/Twitter, YouTube, TikTok, Instagram, HN, Polymarket, Web

---

## ⚠️ 前置检查

```bash
# 核心依赖
pip install yt-dlp requests python-dotenv

# 可选（有则数据更丰富）
# SCRAPECREATORS_API_KEY → Reddit/TikTok/Instagram 数据
# XAI_API_KEY            → X/Twitter 数据
```

缺少 API Key 时，对应平台数据会跳过，**不影响其他平台**。

---

## 📁 子任务目录

```
trend-research/
├── SKILL.md           ← 你在这里（Layer 2）
├── quick-mode/        → 快速模式（--quick）
└── deep-mode/         → 深度模式（--deep）
```
