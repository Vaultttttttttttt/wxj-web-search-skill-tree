# GitHub CLI — Layer 3 叶子节点

**工具**：`gh`（GitHub 官方 CLI）

## 安装与认证

```bash
brew install gh
gh auth login
```

## 仓库信息

```bash
gh repo view owner/repo                    # 仓库详情
gh repo view owner/repo --json description,stargazerCount,topics
```

## 搜索

```bash
gh search repos "LLM framework" --language python --limit 10
gh search repos "MCP server" --sort stars --limit 20
gh search code "class Agent" --language python
gh search issues "memory leak" --repo facebook/react
```

## Issues & PR

```bash
gh issue list --repo anthropics/anthropic-sdk-python
gh issue view 123 --repo owner/repo
gh pr list --repo facebook/react --limit 5
gh pr view 456 --repo owner/repo
```

## API 直接调用

```bash
# 获取仓库 README
gh api repos/owner/repo/readme --jq '.content' | base64 -d

# 获取最新 Release
gh api repos/owner/repo/releases/latest --jq '.tag_name,.body'

# 获取 PR 评论
gh api repos/owner/repo/pulls/123/comments
```

## Jina Reader 方式（GitHub 网页）

```bash
# 读取 GitHub 仓库页面（无需 gh 认证）
curl https://r.jina.ai/https://github.com/owner/repo
curl https://r.jina.ai/https://github.com/owner/repo/blob/main/README.md
```

## 错误处理

| 错误 | 解决 |
|------|------|
| `command not found: gh` | `brew install gh && gh auth login` |
| `403 Forbidden` | `gh auth login` 重新认证 |
| API 限流 | 已登录用户限流阈值更高，确保 `gh auth status` 显示已登录 |
