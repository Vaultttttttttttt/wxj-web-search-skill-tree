# Webpage Reader — Layer 3 叶子节点

**工具**：Jina Reader (`https://r.jina.ai/`)
⚡ **无需安装，无需 API Key，直接用 curl**

## 读取任意网页

```bash
# 返回 Markdown 格式的网页正文
curl https://r.jina.ai/https://example.com

# 实际示例
curl https://r.jina.ai/https://github.com/anthropics/anthropic-sdk-python
curl https://r.jina.ai/https://docs.python.org/3/library/asyncio.html
curl https://r.jina.ai/https://news.ycombinator.com
```

## 适用场景

- 用户提供了一个链接，要求"帮我看看这个"
- 需要读取官方文档某页内容
- 需要提取新闻文章正文
- 任何 http/https 链接的内容读取

## 注意事项

- **登录限制**：需要登录的页面（如 Google Docs、私有 Confluence）无法读取
- **动态页面**：部分 SPA 单页应用可能只返回 JS 骨架，这时请改用 `video-content`（若是视频）或联系用户
- **大文件**：PDF/图片等二进制文件不适用此方法

## 备用方案

若 Jina Reader 失败，尝试：
```bash
# UltimateSearch 的 web-fetch（支持更多场景）
bash /Users/wxj/Documents/skills测试/UltimateSearchSkill/scripts/web-fetch.sh \
  --url "https://example.com"
```

## 错误处理

| 错误 | 解决 |
|------|------|
| 返回内容为空/JS框架 | 改用 `web-fetch.sh` 或通知用户该页需要登录 |
| `curl: command not found` | `brew install curl` |
| 网络超时 | 检查代理设置 |
