# Social Search — Layer 3 叶子节点

**脚本根目录**：`${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/`

## Reddit

```bash
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/duckduckgo/duckduckgo_search.py \
  "site:reddit.com 关键词" --limit 10
```

## YouTube

```bash
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/youtube/youtube_search.py \
  --keyword "AI tutorial" --limit 10

pip install yt-dlp    # 若缺失
```

## 小红书（需要 TikHub Token）

```bash
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/xiaohongshu/tikhub_xhs_search.py \
  --keyword "关键词" --limit 10

# 需要环境变量：TIKHUB_TOKEN
```

## B站

```bash
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/bilibili/bilibili_search.py \
  --keyword "rust" --limit 10
```

## AI驱动搜索

```bash
# Tavily（需要 TAVILY_API_KEY）
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/tavily_search/tavily_search.py \
  --query "AI 2026" --limit 10

# Metaso（秘塔搜索）
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/metaso/metaso_search.py \
  --query "人工智能趋势"

# Google（需要 GOOGLE_API_KEY + GOOGLE_SEARCH_ENGINE_ID）
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/google_search/google_search.py \
  --query "Claude Code" --limit 10
```

## 联合社交搜索

```bash
cd ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}
python scripts/union_search/union_search.py "AI tools" --group social --limit 5
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `TIKHUB_TOKEN not set` | 小红书/抖音需要 TikHub Token，其他平台不受影响 |
| `ModuleNotFoundError` | `pip install requests python-dotenv duckduckgo-search yt-dlp` |
