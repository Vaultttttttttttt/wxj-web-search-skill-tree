# Trend Research Deep Mode — Layer 3 叶子节点

**执行脚本**：`${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py`

## 功能
深度研究某话题近30天的全平台社交媒体讨论，约5分钟出完整报告。
数据来源：Reddit, X/Twitter, YouTube, TikTok, Instagram, HN, Polymarket, Web。

## 执行命令

```bash
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "你的话题" --deep --emit=compact --no-native-web \
  --save-dir=~/Documents/Last30Days
```

## 示例

```bash
# 深度研究 "LLM benchmarks" 近30天
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "LLM benchmarks" --deep --emit=compact --no-native-web

# 关注特定 Twitter 账号 + 话题
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "Midjourney" --x-handle=midjourney --deep --emit=compact --no-native-web

# Agent 模式（非交互，直接输出完整报告）
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "AI video tools" --agent --deep --emit=compact --no-native-web
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--deep` | 深度模式，获取更多数据 |
| `--emit=compact` | 紧凑输出格式 |
| `--no-native-web` | 不使用原生浏览器 |
| `--x-handle=账号名` | 关注特定 Twitter 账号的内容 |
| `--agent` | Agent 模式，无交互直接出报告 |
| `--days=N` | 自定义天数（默认30） |

## 所需 API Keys（可选）

| Key | 覆盖平台 |
|-----|---------|
| `SCRAPECREATORS_API_KEY` | Reddit + TikTok + Instagram（前100次免费，申请：scrapecreators.com） |
| `XAI_API_KEY` | X/Twitter（已在 Intel_Briefing/.env 中配置） |

缺少 Key 时对应平台跳过，**不影响其他平台**。

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError: yt_dlp` | `pip install yt-dlp` |
| 运行5分钟以上无输出 | 加 `--quick` 降级到快速模式 |
