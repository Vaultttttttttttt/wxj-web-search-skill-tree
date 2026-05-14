# Deep Search — Layer 2 路由（AI深度搜索）

## 🧭 你在哪里
`web-search-innospark-tree/deep-search/`
工具来源：UltimateSearch (`/Users/wxj/Documents/skills测试/UltimateSearchSkill/`)

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| 需要Grok AI实时搜索+AI分析总结 | `grok-search` | `./grok-search/SKILL.md` |
| 需要结构化搜索结果+网页内容摘取 | `tavily-search` | `./tavily-search/SKILL.md` |
| 需要双引擎交叉验证、最高可靠性 | `dual-search` | `./dual-search/SKILL.md` |

---

## 🔀 路由决策

```
"最新/实时" + 需要AI解读        → grok-search
需要结构化结果/多链接汇总        → tavily-search
重要决策/需要交叉验证/深度报告   → dual-search（推荐，最可靠）
不确定时                         → dual-search
```

---

## ⚠️ 前置检查：直连模式（无需本地代理）

```bash
# 设置直连 API（若本地代理服务未启动时使用）
export GROK_API_URL=https://api.x.ai/v1
export GROK_API_KEY=your_xai_api_key_here
export GROK_MODEL=grok-4-1-fast
export TAVILY_API_URL=https://api.tavily.com
export TAVILY_API_KEY=your_tavily_api_key_here
```

或加载 .env：
```bash
cd /Users/wxj/Documents/skills测试/UltimateSearchSkill
set -a && source .env && set +a
```

---

## 📁 子任务目录

```
deep-search/
├── SKILL.md           ← 你在这里（Layer 2）
├── grok-search/       → Grok AI实时搜索
├── tavily-search/     → Tavily结构化搜索
└── dual-search/       → 双引擎并行搜索（推荐）
```
