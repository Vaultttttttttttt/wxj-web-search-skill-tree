# Grok Search — Layer 3 叶子节点

**执行脚本**：`${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh`

## 功能
调用 Grok AI 进行实时网络搜索，返回 AI 综合分析结果（非列表，而是段落式总结）。
适合：需要 AI 解读的复杂问题、最新动态、技术对比。

## 执行命令

```bash
# 设置环境（直连模式）
export GROK_API_URL=https://api.x.ai/v1
export GROK_API_KEY=your_xai_api_key_here
export GROK_MODEL=grok-4-1-fast

# 执行搜索
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "你的搜索问题"
```

## 示例

```bash
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "Claude Code 最新特性 2026"

bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "最新 LLM 框架对比"
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `Connection refused 127.0.0.1:8100` | 已设置直连：`export GROK_API_URL=https://api.x.ai/v1` |
| `bash: No such file` | 检查路径：`ls ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/` |
| API 认证错误 | 检查 `GROK_API_KEY` 是否正确 |
