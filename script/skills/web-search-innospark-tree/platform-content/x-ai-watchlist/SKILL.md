# X AI Watchlist — Layer 3 叶子节点

面向“固定人物池”的 X 动态巡检。适合你这个「39 位 AI 人物」场景。

## 能力

- 批量读取人物名单（CSV）
- 按人抓取 X 最新言论（优先 `opencli-rs`，自动降级 `opencli` / `bird`）
- 输出可读摘要和原始结果，便于每日巡检

## 文件约定

- 人物清单：`data/x_ai_people_39.csv`
- 监控脚本：`scripts/x_ai_watchlist.py`
- 导入脚本：`scripts/import_ai39_from_text.py`
- 输出目录：`outputs/x-ai-watchlist/`

## 快速使用

```bash
# 0) 一键入口（推荐）
bash scripts/run_x_ai_watchlist.sh --max-people 5 --limit 3

# 1) 先跑 5 人检查连通性
python3 scripts/x_ai_watchlist.py --max-people 5 --limit 3

# 2) 全量跑 39 人
python3 scripts/x_ai_watchlist.py --limit 5

# 3) 指定驱动（可选）
python3 scripts/x_ai_watchlist.py --driver opencli-rs
python3 scripts/x_ai_watchlist.py --driver bird
```

输出结果在：

- `outputs/x-ai-watchlist/latest/summary.md`
- `outputs/x-ai-watchlist/latest/*.preview.md`
- `outputs/x-ai-watchlist/latest/*.raw.txt`

## 从公众号正文快速导入名单

微信链接经常有环境验证，建议你把文章正文复制到本地文本后导入：

```bash
python3 scripts/import_ai39_from_text.py \
  --input /path/to/wechat_article.txt \
  --output data/x_ai_people_39.csv
```

导入后手工确认 `x_handle` 是否准确。

## 人物清单格式

```csv
name,x_handle,enabled,notes
Sam Altman,sama,1,example
```

- `enabled=1` 表示参与抓取
- `x_handle` 不带 `@`

## 建议定时任务（macOS）

```bash
# 每天早上 8 点执行
0 8 * * * cd /Users/wxj/Documents/skills测试/web-search-innospark-tree && /usr/bin/python3 scripts/x_ai_watchlist.py --limit 5
```
