#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
    ""|--docs-only) ;;
    *) echo "Usage: bash agents/sync.sh [--docs-only]" >&2; exit 2 ;;
esac

echo "[easel] 无需同步：技能保留在 skills/builtin/ 与 skills/shared/，工作区规则保留在 agents/workspace/。"
