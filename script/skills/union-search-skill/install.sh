#!/usr/bin/env bash
set -e

CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
SKILL_NAME="union-search-skill"
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

# 创建 skills 目录（如果不存在）
mkdir -p "$CLAUDE_HOME/skills"

# 软链接整个 skill 目录到 Claude skills 下
ln -sfn "$SKILL_DIR" "$CLAUDE_HOME/skills/$SKILL_NAME"

echo "✅ Skill linked: $CLAUDE_HOME/skills/$SKILL_NAME -> $SKILL_DIR"
echo "✅ 安装完成，重启 Claude Code 后生效"
