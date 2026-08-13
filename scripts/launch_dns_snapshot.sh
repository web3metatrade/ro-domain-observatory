#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${1:?run ID is required}"
RUN_DIR="$ROOT/data/dns/$RUN_ID"
mkdir -p "$RUN_DIR"
chmod +x "$ROOT/scripts/run_dns_snapshot.sh" "$ROOT/scripts/pace_lines.py"
nohup bash "$ROOT/scripts/run_dns_snapshot.sh" "$RUN_ID" \
  > "$RUN_DIR/console.log" 2>&1 < /dev/null &
printf '%s\n' "$!"
