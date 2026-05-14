# Video Content — Layer 3 叶子节点

**工具**：`yt-dlp`（支持 YouTube、B站及1000+平台）

## 安装

```bash
pip install yt-dlp
# 或
brew install yt-dlp
```

## YouTube

```bash
# 获取视频基本信息（标题、时长、描述、播放量等）
yt-dlp --dump-json "https://youtube.com/watch?v=xxx"

# 下载字幕（不下载视频）
yt-dlp --write-sub --skip-download "https://youtube.com/watch?v=xxx"

# 指定语言字幕
yt-dlp --write-sub --sub-lang zh-Hans,en --skip-download "URL"

# 获取所有可用字幕列表
yt-dlp --list-subs "URL"

# OpenCLI 方式（需要 Chrome 扩展）
opencli youtube transcript "https://youtube.com/watch?v=xxx"
opencli youtube transcript "xxx" --lang zh-Hans --mode raw
```

## B站

```bash
# 视频信息
yt-dlp --dump-json "https://www.bilibili.com/video/BVxxx"

# 字幕
yt-dlp --write-sub --skip-download "https://www.bilibili.com/video/BVxxx"

# OpenCLI 方式
opencli bilibili subtitle --bvid BV1xxx
opencli bilibili subtitle --bvid BV1xxx --lang zh-CN
```

## 其他平台
yt-dlp 支持 Twitter、Instagram、TikTok 等 1000+ 平台，命令格式相同。

## 错误处理

| 错误 | 解决 |
|------|------|
| `command not found: yt-dlp` | `pip install yt-dlp` |
| 字幕不存在 | 尝试 `--write-auto-sub`（自动生成字幕） |
| 403 / 私有视频 | 需要 cookies，`--cookies-from-browser chrome` |
