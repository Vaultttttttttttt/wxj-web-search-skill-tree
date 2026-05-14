# Alpha Radar — Layer 3 叶子节点

**执行脚本**：`/Users/wxj/Documents/skills测试/Intel_Briefing/run_alpha_radar.py`

## 功能
用 Grok 搜索 Web3/Solana/开源早期项目，识别尚未被广泛发现的 Alpha 机会。
输出：项目列表、阶段评估、风险提示、行动建议。

## 执行命令

```bash
cd /Users/wxj/Documents/skills测试/Intel_Briefing && python run_alpha_radar.py
```

## 环境要求

| 变量 | 必须 |
|------|:----:|
| `GITHUB_TOKEN` | ✅ |
| `XAI_API_KEY` | ✅（核心功能，缺少则大量跳过） |

## 错误处理

| 错误 | 解决 |
|------|------|
| `XAI_API_KEY not set` | Alpha Radar 核心依赖 Grok，建议配置后再运行 |
| `ModuleNotFoundError` | `pip install -r /Users/wxj/Documents/skills测试/Intel_Briefing/requirements.txt` |
