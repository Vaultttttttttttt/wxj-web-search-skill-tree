# Public API — Layer 3 叶子节点

**工具**：`opencli-rs`（推荐）或 `opencli`
⚡ **无需 Chrome 浏览器，直接可用**

## 使用方式
```bash
export PATH="$HOME/bin:$PATH"
# 以下命令均可将 opencli-rs 替换为 opencli，效果相同
```

## Hacker News
```bash
opencli-rs hackernews top --limit 10
```

## V2EX
```bash
opencli-rs v2ex hot --limit 10
opencli-rs v2ex latest --limit 10
opencli-rs v2ex topic <id>           # 话题详情+回复
```

## Google
```bash
opencli-rs google news --limit 10
opencli-rs google search "关键词"
opencli-rs google trends
```

## 其他公开平台
```bash
opencli-rs bbc news --limit 10
opencli-rs reuters search "AI"
opencli-rs devto top --limit 10
opencli-rs lobsters hot --limit 10
opencli-rs stackoverflow hot --limit 10
opencli-rs stackoverflow search "关键词"
opencli-rs hf top --limit 10               # HuggingFace 热门模型
opencli-rs steam top-sellers --limit 10
opencli-rs wikipedia search "关键词"
opencli-rs wikipedia summary "Python"
opencli-rs linux-do hot --limit 10
opencli-rs linux-do search "rust"
opencli-rs arxiv search "LLM" --limit 10   # arXiv 论文（新增）
opencli-rs bloomberg news --limit 10        # 彭博新闻（新增）
```

## Grok（AI问答）
```bash
opencli-rs grok ask --prompt "你的问题"
```

## 管理命令
```bash
opencli-rs --help          # 列出所有支持站点
opencli-rs doctor          # 诊断连接状态
opencli-rs completion zsh  # 生成 zsh 补全
```

## 通用参数
所有命令支持：`--format table|json|yaml|md|csv`

## 错误处理

| 错误 | 解决 |
|------|------|
| `command not found: opencli-rs` | `export PATH="$HOME/bin:$PATH"` 或用 `~/bin/opencli-rs` |
| `command not found: opencli` | `npm install -g @jackwener/opencli` |
