#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ULTIMATE_DIR="/Users/wxj/Documents/skills测试/UltimateSearchSkill"
ULTIMATE_DIR="${ULTIMATE_SEARCH_DIR:-$DEFAULT_ULTIMATE_DIR}"

if [[ ! -d "$ULTIMATE_DIR" ]]; then
  echo '{"error":"UltimateSearchSkill 目录不存在，请设置 ULTIMATE_SEARCH_DIR"}'
  exit 1
fi

DUAL_SCRIPT="$ULTIMATE_DIR/scripts/dual-search.sh"
GROK_SCRIPT="$ULTIMATE_DIR/scripts/grok-search.sh"
TAVILY_SCRIPT="$ULTIMATE_DIR/scripts/tavily-search.sh"

QUERY=""
TAVILY_DEPTH="basic"
TAVILY_MAX_RESULTS=8

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query)
      QUERY="${2:-}"
      shift 2
      ;;
    --tavily-depth)
      TAVILY_DEPTH="${2:-basic}"
      shift 2
      ;;
    --tavily-max-results|--max-results)
      TAVILY_MAX_RESULTS="${2:-8}"
      shift 2
      ;;
    --help)
      cat <<EOF
用法: $(basename "$0") --query "查询内容" [--tavily-depth basic|advanced] [--tavily-max-results N]
EOF
      exit 0
      ;;
    *)
      shift 1
      ;;
  esac
done

if [[ -z "$QUERY" ]]; then
  echo '{"error":"缺少参数 --query"}'
  exit 1
fi

to_json_or_error() {
  local raw="$1"
  local label="$2"
  local trimmed
  trimmed="$(echo "$raw" | sed '/^[[:space:]]*$/d')"
  if [[ -z "$trimmed" ]]; then
    jq -n --arg msg "$label returned empty output" '{error:$msg}'
    return
  fi
  if echo "$raw" | jq empty >/dev/null 2>&1; then
    echo "$raw"
    return
  fi
  jq -n --arg msg "$label invalid json output" --arg raw "$(echo "$raw" | head -n 30)" '{error:$msg, raw:$raw}'
}

# 优先尝试 dual-search
if [[ -x "$DUAL_SCRIPT" ]]; then
  dual_out="$(bash "$DUAL_SCRIPT" --query "$QUERY" --tavily-depth "$TAVILY_DEPTH" --tavily-max-results "$TAVILY_MAX_RESULTS" 2>&1 || true)"
  if [[ -n "$dual_out" ]] && echo "$dual_out" | jq empty >/dev/null 2>&1; then
    echo "$dual_out"
    exit 0
  fi
fi

# dual-search 失败后，降级为单引擎并包装成稳定 JSON
grok_raw=""
tavily_raw=""
if [[ -x "$GROK_SCRIPT" ]]; then
  grok_raw="$(bash "$GROK_SCRIPT" --query "$QUERY" 2>&1 || true)"
fi
if [[ -x "$TAVILY_SCRIPT" ]]; then
  tavily_raw="$(bash "$TAVILY_SCRIPT" --query "$QUERY" --depth "$TAVILY_DEPTH" --max-results "$TAVILY_MAX_RESULTS" --include-answer 2>&1 || true)"
fi

grok_json="$(to_json_or_error "$grok_raw" "grok-search.sh")"
tavily_json="$(to_json_or_error "$tavily_raw" "tavily-search.sh")"

jq -n \
  --argjson grok "$grok_json" \
  --argjson tavily "$tavily_json" \
  '{grok:$grok, tavily:$tavily}'
