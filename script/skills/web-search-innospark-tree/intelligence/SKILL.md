# Intelligence — Layer 2 路由（情报与简报系统）

## 🧭 你在哪里
`web-search-innospark-tree/intelligence/`
工具来源：Intel Briefing (`/Users/wxj/Documents/skills测试/Intel_Briefing/`)

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| 每日简报、每日情报、今日趋势汇总、全面日报 | `daily-briefing` | `./daily-briefing/SKILL.md` |
| V2EX悬赏、Chrome扩展机会、接单赚钱 | `bounty-hunter` | `./bounty-hunter/SKILL.md` |
| Web3、Solana、开源Alpha机会、早期项目 | `alpha-radar` | `./alpha-radar/SKILL.md` |
| 读取简报后找商业机会、5类变现方向、收入分析 | `revenue-architect` | `./revenue-architect/SKILL.md` |

---

## 🔀 路由决策

```
用户要"今日/每日简报"        → daily-briefing
用户要"赚钱机会/接单/悬赏"   → bounty-hunter
用户要"Web3/Alpha/链上"      → alpha-radar
用户要"变现/收入/商业机会"   → revenue-architect
不确定时                      → daily-briefing（最全面）
```

---

## ⚠️ 前置检查

所有子任务执行前确认：
```bash
# 检查 .env 是否存在且含 GITHUB_TOKEN
cat /Users/wxj/Documents/skills测试/Intel_Briefing/.env | grep GITHUB_TOKEN
```
如果 GITHUB_TOKEN 缺失，**所有子任务均无法运行**，提示用户配置。

---

## 📁 子任务目录

```
intelligence/
├── SKILL.md               ← 你在这里（Layer 2）
├── daily-briefing/        → 每日综合情报简报
├── bounty-hunter/         → V2EX悬赏 + Chrome扩展机会
├── alpha-radar/           → Web3/Solana Alpha雷达
└── revenue-architect/     → 商业机会分析师
```
