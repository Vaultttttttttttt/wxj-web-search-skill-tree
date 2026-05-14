# Platform Content — Layer 2 路由（平台内容获取）

## 🧭 你在哪里
`web-search-innospark-tree/platform-content/`

## ⚡ 工具选择（优先用 opencli-rs）

| 工具 | 安装 | 速度 | 内存 | 依赖 |
|------|------|------|------|------|
| **opencli-rs** ✅ 推荐 | `~/bin/opencli-rs` (已安装 v0.1.1) | 12x 快 | 15MB | 无运行时 |
| opencli (JS) | `opencli` | 基准 | 99MB | Node.js |

两者**命令结构相同**，仅 binary 名称不同。优先用 `opencli-rs`，不可用时降级到 `opencli`。

**路径**：`~/bin/opencli-rs`（或将 `~/bin` 加入 PATH 后直接用 `opencli-rs`）
```bash
export PATH="$HOME/bin:$PATH"
```

**Chrome 扩展**：opencli-rs 与 opencli 共用同一个扩展，**已安装 opencli 扩展的用户无需重复安装**。

---

## 📋 子任务路由表

| 平台 / 任务意图 | 子任务 | 读取路径 |
|--------------|--------|---------|
| B站、知乎、小红书、微博、即刻、豆瓣 | `chinese-platforms` | `./chinese-platforms/SKILL.md` |
| Twitter/X、Reddit、YouTube、Instagram、TikTok、Medium | `global-social` | `./global-social/SKILL.md` |
| 39位AI人物、固定人物池追踪、X人物言论监控 | `x-ai-watchlist` | `./x-ai-watchlist/SKILL.md` |
| 雪球、股票行情、Yahoo Finance、Barchart、期权流 | `finance-markets` | `./finance-markets/SKILL.md` |
| Hacker News、V2EX、GitHub、Google、Wikipedia、DEV.to（无需浏览器） | `public-api` | `./public-api/SKILL.md` |

---

## 🔀 路由决策

```
B站/知乎/小红书/微博/国内平台    → chinese-platforms
Twitter/Reddit/YouTube/海外社交  → global-social
固定人物池/X监控/AI人物追踪     → x-ai-watchlist
股票/行情/期权/金融数据           → finance-markets
HN/V2EX/GitHub/通用搜索          → public-api（最快，无需浏览器）
```

---

## ⚠️ 前置检查

```bash
# 验证 opencli-rs（推荐）
export PATH="$HOME/bin:$PATH"
opencli-rs --version   # 应显示 0.1.1

# 若找不到，指定完整路径
~/bin/opencli-rs --version

# 需要浏览器的平台：检查 Chrome 扩展连接
opencli-rs doctor

# 降级：验证 opencli (JS)
opencli --version
```

---

## 📁 子任务目录

```
platform-content/
├── SKILL.md               ← 你在这里（Layer 2）
├── chinese-platforms/     → B站/知乎/小红书/微博等
├── global-social/         → Twitter/Reddit/YouTube等
├── x-ai-watchlist/        → X 固定人物池监控（39位AI人物）
├── finance-markets/       → 股票/行情/金融数据
└── public-api/            → 无需浏览器的公开API平台
```
