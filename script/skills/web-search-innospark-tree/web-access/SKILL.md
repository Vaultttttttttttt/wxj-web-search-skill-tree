# Web Access — Layer 2 路由（网页/视频/RSS访问）

## 🧭 你在哪里
`web-search-innospark-tree/web-access/`
工具来源：Agent-Reach + Jina Reader + yt-dlp + bird + gh CLI

---

## 📋 子任务路由表

| 任务意图 | 子任务 | 读取路径 |
|---------|--------|---------|
| 读取任意网页链接、提取正文内容 | `webpage-reader` | `./webpage-reader/SKILL.md` |
| YouTube/B站视频字幕、视频信息、音频 | `video-content` | `./video-content/SKILL.md` |
| Twitter/X内容、RSS订阅源 | `social-stream` | `./social-stream/SKILL.md` |
| GitHub仓库信息、Issue、PR、代码搜索 | `github-cli` | `./github-cli/SKILL.md` |

---

## 🔀 路由决策

```
用户给了一个 http/https 链接      → webpage-reader（最通用）
youtube.com / bilibili.com 链接   → video-content
x.com / twitter.com 链接或推文    → social-stream
github.com 链接或 gh 相关操作     → github-cli
RSS/Atom feed URL                 → social-stream
```

**优先级**：先判断链接类型，再选子任务。

---

## ⚠️ 前置检查

```bash
# webpage-reader：无需安装，直接用 curl
curl https://r.jina.ai/https://example.com

# video-content：需要 yt-dlp
yt-dlp --version || pip install yt-dlp

# social-stream（Twitter）：需要 bird
bird --version || npm install -g @steipete/bird

# github-cli：需要 gh
gh --version || brew install gh
```

---

## 📁 子任务目录

```
web-access/
├── SKILL.md               ← 你在这里（Layer 2）
├── webpage-reader/        → 任意网页读取（Jina Reader）
├── video-content/         → YouTube/B站视频内容（yt-dlp）
├── social-stream/         → Twitter/RSS订阅（bird + feedparser）
└── github-cli/            → GitHub操作（gh CLI）
```
