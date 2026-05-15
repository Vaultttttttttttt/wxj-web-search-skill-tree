# Dual Search — Layer 3 叶子节点

**执行脚本**：`${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/dual-search.sh`

## 功能
同时调用 Grok + Tavily 并行搜索，交叉验证结果，输出最高可靠性的综合报告。
适合：重要决策参考、技术选型、深度调研报告。

## 执行命令

```bash
# 设置环境（直连模式，两个都需要）
export GROK_API_URL=https://api.x.ai/v1
export GROK_API_KEY=your_xai_api_key_here
export GROK_MODEL=grok-4-1-fast
export TAVILY_API_URL=https://api.tavily.com
export TAVILY_API_KEY=your_tavily_api_key_here

# 执行双引擎搜索
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/dual-search.sh \
  --query "LangChain vs LlamaIndex 2026"
```

## 示例

```bash
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/dual-search.sh \
  --query "React Server Components 最佳实践"

bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/dual-search.sh \
  --query "Claude Code 与 Cursor 对比"
```

## Web Fetch（网页全文抓取）

```bash
# 获取任意网页完整内容（Markdown格式）
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/web-fetch.sh \
  --url "https://docs.langchain.com/docs/"

# 网站结构地图
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/web-map.sh \
  --url "https://docs.example.com" --depth 2
```

## 错误处理

| 错误 | 解决 |
|------|------|
| 任一引擎失败 | 另一引擎仍会继续，输出单引擎结果 |
| `Connection refused` | 设置直连环境变量（见上方） |
