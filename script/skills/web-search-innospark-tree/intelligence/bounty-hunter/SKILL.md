# Bounty Hunter — Layer 3 叶子节点

**执行脚本**：`/Users/wxj/Documents/skills测试/Intel_Briefing/run_bounty_hunter.py`

## 功能
自动扫描 V2EX 悬赏板块 + Chrome 扩展市场，寻找短期接单变现机会。
输出：需求列表、预算范围、技术难度评估、接单建议。

## 执行命令

```bash
cd /Users/wxj/Documents/skills测试/Intel_Briefing && python run_bounty_hunter.py
```

## 环境要求

| 变量 | 必须 |
|------|:----:|
| `GITHUB_TOKEN` | ✅ |
| `XAI_API_KEY` | 推荐（AI分析机会质量） |

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install -r /Users/wxj/Documents/skills测试/Intel_Briefing/requirements.txt` |
| `GITHUB_TOKEN not set` | 在 `.env` 中添加 `GITHUB_TOKEN=your_github_token_here` |
