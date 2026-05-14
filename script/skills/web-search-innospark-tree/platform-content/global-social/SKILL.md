# Global Social — Layer 3 叶子节点

## ⚡ 工具优先级

| 平台 | 首选工具 | 备用 |
|------|---------|------|
| Twitter/X | **grok-search.sh**（联网实时抓取） | opencli-rs（需 Chrome 扩展） |
| Reddit | grok-search.sh | opencli-rs |
| YouTube/Instagram/TikTok | opencli-rs | — |
| Medium/Substack/LinkedIn | opencli-rs | — |

---

## Twitter / X（首选：Grok 联网搜索）

```bash
# 搜索话题推文
bash /Users/wxj/Documents/skills测试/UltimateSearchSkill/scripts/grok-search.sh \
  --query "搜索X/Twitter上关于[关键词]的最新推文，返回原文、作者、时间和链接"

# 抓某人最近推文
bash /Users/wxj/Documents/skills测试/UltimateSearchSkill/scripts/grok-search.sh \
  --query "搜索@[handle] 在X上最近5条推文，返回原文、时间和推文链接"

# 热门话题
bash /Users/wxj/Documents/skills测试/UltimateSearchSkill/scripts/grok-search.sh \
  --query "X/Twitter今日[话题]热门讨论，列出最有影响力的推文原文和链接"
```

## Reddit（首选：Grok 联网搜索）

```bash
bash /Users/wxj/Documents/skills测试/UltimateSearchSkill/scripts/grok-search.sh \
  --query "搜索Reddit上关于[关键词]的热门帖子，返回标题、摘要和链接"
```

## YouTube

```bash
export PATH="$HOME/bin:$PATH"
opencli-rs youtube search "关键词"
opencli-rs youtube transcript "https://youtube.com/watch?v=xxx"
```

## Instagram / TikTok / LinkedIn

```bash
export PATH="$HOME/bin:$PATH"
opencli-rs instagram profile <username>
opencli-rs tiktok search "关键词"
opencli-rs linkedin search "关键词"
opencli-rs medium feed --limit 10
```

---

## ⚠️ 前置条件

```bash
# grok-search.sh 前置：确认 grok2api 在运行
curl -s http://127.0.0.1:8100/v1/models | head -1

# opencli 前置（YouTube/Instagram 等）
opencli-rs doctor
```

## 错误处理

| 错误 | 解决 |
|------|------|
| grok2api 连接失败 | `cd UltimateSearchSkill && docker compose up -d grok2api` |
| `Extension not connected` | 检查 Chrome + OpenCLI 扩展（仅 YouTube/Instagram 需要） |
| Twitter 数据为空 | 改用 grok-search.sh，不需要 Chrome |
