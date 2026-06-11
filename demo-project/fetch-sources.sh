#!/usr/bin/env bash
# 拉取 demo-project 源码仓库（Apache Fineract 等），依据 config.yaml + repos.manifest.yaml。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
CONFIG="${SKILL_LAB_CONFIG_PATH:-$REPO_ROOT/config.yaml}"
exec python3 "$ROOT/fetch_sources.py" --config-path "$CONFIG" "$@"
