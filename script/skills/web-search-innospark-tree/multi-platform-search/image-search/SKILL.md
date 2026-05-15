# Image Search — Layer 3 叶子节点

**脚本**：`${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/union_image_search/multi_platform_image_search.py`
⚡ **18个平台，无需 API Key，直接可用**

## 基础用法

```bash
# 从所有18个平台批量搜索下载
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/union_image_search/multi_platform_image_search.py \
  --keyword "cats" --num 50

# 指定平台搜索
python ${WEB_SEARCH_UNION_ROOT:-../union-search-skill}/scripts/union_image_search/multi_platform_image_search.py \
  --keyword "AI technology" \
  --platforms pixabay,unsplash,bing \
  --num 30
```

## 支持的平台（部分）

| 平台 | 说明 |
|------|------|
| `pixabay` | 免费商用图片 |
| `unsplash` | 高质量摄影图片 |
| `bing` | Bing图片搜索 |
| `pexels` | 免费图片视频 |
| 其余14个平台 | 见脚本 `--list-platforms` |

## 参数说明

| 参数 | 说明 |
|------|------|
| `--keyword` | 搜索关键词 |
| `--num N` | 每平台下载数量 |
| `--platforms a,b,c` | 指定平台（逗号分隔），默认全部 |
| `--output-dir` | 保存目录 |

## 前置依赖

```bash
pip install requests python-dotenv
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `ModuleNotFoundError` | `pip install requests python-dotenv` |
| 某平台下载失败 | 该平台跳过，其他平台继续下载 |
