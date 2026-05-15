# Trend Research Quick Mode — Layer 3 叶子节点

**执行脚本**：`${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py`

## 功能
快速研究某话题近30天的社交媒体讨论热度，约1-2分钟出结果。
数据来源：Reddit, YouTube, HN, Web（跳过 TikTok/Instagram/Twitter）。

## 执行命令

```bash
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "你的话题" --quick --emit=compact --no-native-web \
  --save-dir=~/Documents/Last30Days
```

## 示例

```bash
# 研究 "Claude Code" 近30天的讨论
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "Claude Code" --quick --emit=compact --no-native-web

# 7天内的讨论（更聚焦）
python3 ${LAST30DAYS_SKILL_ROOT:-../last30days-skill}/scripts/last30days.py \
  "Vibe coding" --days=7 --quick --emit=compact --no-native-web
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--quick` | 快速模式，减少数据量 |
| `--emit=compact` | 紧凑输出格式 |
| `--no-native-web` | 跳过原生Web抓取，加快速度 |
| `--days=N` | 自定义天数（默认30） |
| `--save-dir=路径` | 保存报告的目录 |

## 前置依赖

```bash
pip install yt-dlp requests python-dotenv
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError: yt_dlp` | `pip install yt-dlp` |
| `SCRAPECREATORS_API_KEY not set` | Reddit/TikTok跳过，其他平台继续 |
| 脚本超时 | quick模式已优化，若仍超时改用 `--days=7` |
