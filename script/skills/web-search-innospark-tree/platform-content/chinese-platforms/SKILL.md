# Chinese Platforms — Layer 3 叶子节点

## ⚡ 无浏览器模式（服务器/无 Chrome 环境）

所有平台优先用 **WebSearch** 或 **Grok 联网搜索**，opencli 作为本地有浏览器时的可选增强。

---

## B站（Bilibili）

```bash
# 关键词搜索视频/内容
WebSearch: site:bilibili.com [关键词]
WebSearch: bilibili [关键词] 2026

# Grok 搜索（获取今日热点）
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "B站今日[关键词]热门视频，列出标题、UP主、播放量和链接"
```

## 知乎

```bash
# 关键词搜索
WebSearch: site:zhihu.com [关键词]
WebSearch: 知乎 [关键词]

# Grok 搜索
bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "知乎今日[关键词]热门问答，列出问题标题、高赞回答摘要和链接"
```

## 小红书

```bash
WebSearch: 小红书 [关键词]
WebSearch: site:xiaohongshu.com [关键词]

bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "小红书上关于[关键词]的热门笔记，列出标题、内容摘要和链接"
```

## 微博

```bash
WebSearch: 微博 [关键词]
WebSearch: site:weibo.com [关键词]

bash ${ULTIMATE_SEARCH_DIR:-../UltimateSearchSkill}/scripts/grok-search.sh \
  --query "微博今日[关键词]热门内容，列出博主、内容原文和链接"
```

## 即刻 / 豆瓣 / 微信公众号

```bash
WebSearch: 即刻 [关键词]
WebSearch: 豆瓣 [关键词]
WebSearch: 微信公众号 [关键词]
```

## 秘塔搜索（中文聚合搜索，推荐）

```python
# 使用 global_search_multi.py 调用 Metaso API
python3 ${WEB_SEARCH_SKILL_ROOT:-.}/scripts/global_search_multi.py \
  --query "[关键词]" --platforms metaso
```

---

## 本地有浏览器时（可选增强）

```bash
export PATH="$HOME/bin:$PATH"
opencli-rs bilibili hot --limit 10
opencli-rs zhihu hot --limit 10
opencli-rs xiaohongshu search "关键词"
opencli-rs weibo hot --limit 10
```

---

## ⚠️ 重要输出规则

执行完搜索后，**必须将每条结果完整输出**：
- 每个平台至少输出 5 条具体条目
- 格式：`序号. 标题/内容原文 | 来源 | 链接`
- 禁止用"已搜索X条"代替具体内容
- 所有内容直接输出在回复中，不要保存到文件
