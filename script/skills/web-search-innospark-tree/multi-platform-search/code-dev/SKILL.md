# Code Dev Search — Layer 3 叶子节点

**脚本根目录**：`/Users/wxj/Documents/skills测试/union-search-skill/`
⚡ **无需 API Key，直接可用**

## DuckDuckGo（最常用，无需Key）

```bash
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/duckduckgo/duckduckgo_search.py \
  "搜索关键词" --limit 10
```

## GitHub 搜索

```bash
# 仓库搜索
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/github/github_search.py \
  repo "llm agent" --language python --stars ">1000"

# 代码搜索
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/github/github_search.py \
  code "transformer" --language python

# 前置依赖
pip install PyGithub
```

## Wikipedia

```bash
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/wikipedia/wikipedia_search.py \
  "transformer architecture"

pip install wikipedia-api   # 若缺失
```

## Bing / Yahoo / Brave

```bash
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/bing/bing_search.py "关键词"
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/yahoo/yahoo_search.py "AI 2026"
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/brave/brave_search.py "LLM"
```

## 联合搜索（同时查多平台）

```bash
cd /Users/wxj/Documents/skills测试/union-search-skill
python scripts/union_search/union_search.py "machine learning" --group dev --limit 3
```

## RSS 搜索

```bash
python /Users/wxj/Documents/skills测试/union-search-skill/scripts/rss_search/rss_search.py \
  --url "https://hnrss.org/frontpage"
```

## 通用参数
`--limit N` 结果数 | `--json` JSON格式 | `--markdown` Markdown | `-o file` 保存文件

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install requests python-dotenv duckduckgo-search` |
| GitHub 403 | 配置 `GITHUB_TOKEN` 环境变量 |
| 403 限流 | `export DUCKDUCKGO_PROXY=http://127.0.0.1:7890` |
