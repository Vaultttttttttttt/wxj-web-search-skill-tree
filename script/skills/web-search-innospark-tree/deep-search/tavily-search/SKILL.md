# Tavily Search — Layer 3 叶子节点

**执行脚本**：`${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/tavily-search.sh`

## 功能
结构化搜索，返回多个链接+摘要+可选的完整网页内容。
适合：需要多源引用、官方文档查询、新闻聚合。

## 执行命令

```bash
# 设置环境（直连模式）
export TAVILY_API_URL=https://api.tavily.com
export TAVILY_API_KEY=your_tavily_api_key_here

# 基础搜索
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/tavily-search.sh \
  --query "搜索关键词" --depth basic --include-answer

# 新闻模式（近期新闻）
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/tavily-search.sh \
  --query "AI 最新进展" --topic news --time-range week

# 深度模式（完整网页内容）
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/tavily-search.sh \
  --query "FastAPI vs Django 2026" --depth advanced
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--depth basic` | 快速搜索，返回摘要 |
| `--depth advanced` | 深度搜索，包含网页全文 |
| `--topic news` | 新闻模式 |
| `--time-range week/month` | 时间过滤 |
| `--include-answer` | 包含 AI 直接回答 |

## 错误处理

| 错误 | 解决 |
|------|------|
| `Connection refused 127.0.0.1:8200` | 已设置直连：`export TAVILY_API_URL=https://api.tavily.com` |
| API 认证错误 | 检查 `TAVILY_API_KEY` |
