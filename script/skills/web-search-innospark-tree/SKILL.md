---
name: web-search-innospark-tree
version: "1.0.0"
description: 综合网络情报与搜索技能树。3层路由结构：Layer1总路由→Layer2子域→Layer3叶子执行节点。覆盖情报简报、平台内容获取、AI深度搜索、多平台批量搜索、网页访问、趋势研究、新闻聚合。
author: InnoSpark
tags:
  - search
  - intel
  - news
  - research
  - bilibili
  - twitter
  - reddit
  - github
  - youtube
  - grok
  - tavily
metadata:
  skillRoot: ~/.claude/skills/web-search-innospark-tree
  requires:
    env:
      - GITHUB_TOKEN
    optionalEnv:
      - XAI_API_KEY
      - TAVILY_API_KEY
      - GROK_API_KEY
      - TIKHUB_TOKEN
      - SCRAPECREATORS_API_KEY
---

# Web Search InnoSpark — 技能树 Layer 1（总路由）

> **技能树根目录**：`~/.claude/skills/web-search-innospark-tree/`
> 需要读取子节点时，使用 Read 工具加载对应的绝对路径 SKILL.md。

## 🧭 你在哪里
这是技能树的**根节点**。你的职责：
1. 判断任务属于哪个子域
2. 读取对应子域的 Layer 2 SKILL.md
3. 多任务时并行处理多个子域

---

## 📋 子域路由表

| 任务关键词 / 意图 | 子域 | 读取路径 |
|----------------|------|---------|
| 每日简报、情报日报、趋势汇总、V2EX悬赏、Web3 Alpha、商业机会分析 | `intelligence` | `~/.claude/skills/web-search-innospark-tree/intelligence/SKILL.md` |
| B站、知乎、小红书、微博、Twitter/X、Reddit、YouTube、HN、行情、股票、39位AI人物、X人物监控、账号池巡检 | `platform-content` | `~/.claude/skills/web-search-innospark-tree/platform-content/SKILL.md` |
| AI深度分析、交叉验证、实时搜索+AI总结、最新文档/版本 | `deep-search` | `~/.claude/skills/web-search-innospark-tree/deep-search/SKILL.md` |
| 跨平台批量搜索、GitHub代码搜索、图片批量下载、多平台同时查 | `multi-platform-search` | `~/.claude/skills/web-search-innospark-tree/multi-platform-search/SKILL.md` |
| 读取网页链接、视频字幕、RSS订阅、GitHub仓库信息 | `web-access` | `~/.claude/skills/web-search-innospark-tree/web-access/SKILL.md` |
| 某话题近30天讨论、社交媒体热度变化、近期趋势 | `trend-research` | `~/.claude/skills/web-search-innospark-tree/trend-research/SKILL.md` |
| 今日新闻、早报、Hacker News、科技新闻聚合 | `news` | `~/.claude/skills/web-search-innospark-tree/news/SKILL.md` |

---

## 🔀 路由决策流程

```
收到任务
  ↓
Step 1: 扫描任务中的关键词，匹配上方路由表
  ↓
Step 2: 确定匹配的子域（可能是1个或多个）
  ↓
Step 3: 读取对应子域的 SKILL.md（Layer 2）
  ↓
Step 4: Layer 2 会进一步路由到 Layer 3 叶子节点
  ↓
Step 5: 在 Layer 3 执行具体命令
```

---

## ⚡ 多任务处理协议

当任务**同时涉及多个子域**时（如"搜B站视频 + 查今日新闻"）：

```
Step 1: 列出所有匹配的子域
        例：[platform-content, news]

Step 2: 并行读取所有匹配子域的 Layer 2 SKILL.md
        Read ./platform-content/SKILL.md
        Read ./news/SKILL.md          ← 同时读取，不等待

Step 3: 从每个 Layer 2 确定各自需要的 Layer 3 叶子节点
        platform-content → global-social
        news → tech-news

Step 4: 并行读取 Layer 3 SKILL.md，并行执行命令

Step 5: 汇总所有结果，统一展示
```

**原则**：每个子域之间独立执行，互不等待。最后合并输出。

---

## 📤 输出规则（必须遵守）

1. **完整输出每条内容**：不得用"X条结果"、"已搜索完成"代替实际内容
2. **每个平台至少列出 5 条具体条目**，格式：`序号. 标题/原文 | 来源 | 链接`
3. **禁止保存到文件后只输出摘要**：如果脚本生成了文件，用 Read 工具读取后把完整内容输出
4. **不依赖 opencli**：所有平台优先用 WebSearch、WebFetch 或 Grok；opencli 仅作本地可选增强
5. **直接在回复中展示所有内容**，前端会做渲染，无需额外格式化

---

## 🔙 回退机制

```
叶子节点（Layer 3）执行失败
  ↓
回到 Layer 2：查看同域其他子任务是否可完成目标
  ↓
仍失败 → 回到 Layer 1：查看其他子域是否有替代方案
  ↓
告知用户具体失败原因 + 可用替代路径
```

---

## 📁 子域目录

```
web-search-innospark-tree/
├── SKILL.md                    ← 你在这里（Layer 1）
├── intelligence/               → 情报与简报生成
├── platform-content/           → 各平台内容获取（OpenCLI）
├── deep-search/                → AI深度搜索（Grok + Tavily）
├── multi-platform-search/      → 多平台批量搜索
├── web-access/                 → 网页/视频/RSS访问
├── trend-research/             → 近30天话题研究
└── news/                       → 新闻聚合
```
