#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_RUN="${1:?parent DNS run ID is required}"
RUN_DIR="$ROOT/data/dns/$PARENT_RUN"
SOURCE="$RUN_DIR/pass4-local-errors/results.jsonl.gz"
OUTPUT_DIR="$RUN_DIR/pass5-final-local-errors"
INPUT="$OUTPUT_DIR/input.csv"

mkdir -p "$OUTPUT_DIR"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/started_at.txt"
printf 'preparing_pass5_final_local_errors\n' > "$RUN_DIR/state.txt"
python3 "$ROOT/scripts/extract_dns_status.py" \
  --input "$SOURCE" --output "$INPUT" \
  --summary "$OUTPUT_DIR/input-summary.json" --statuses ERROR \
  > "$OUTPUT_DIR/extractor.log" || exit 1

cat > "$OUTPUT_DIR/config.txt" <<EOF
input=$INPUT
input_domains=$(wc -l < "$INPUT")
selected_queries=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_queries"])' "$OUTPUT_DIR/input-summary.json")
threads=5
retries=1
network_timeout_seconds=5
domain_timeout_seconds=30
name_servers=1.1.1.1,8.8.8.8,9.9.9.9
rate_limiter=disabled
EOF

printf 'running_pass5_final_local_errors\n' > "$RUN_DIR/state.txt"
set +e
zdns MULTIPLE --multi-config-file="$ROOT/scripts/zdns-12-types.ini" \
  --threads=5 --retries=1 --network-timeout=5 --timeout=30 \
  --no-follow-cnames --name-servers=1.1.1.1,8.8.8.8,9.9.9.9 \
  --include-fields=protocol,resolver,ttl,flags,dnssec \
  --status-updates-file="$OUTPUT_DIR/status.log" \
  --metadata-file="$OUTPUT_DIR/metadata.json" \
  --input-file="$INPUT" --output-file=- \
  | gzip -1 > "$OUTPUT_DIR/results.jsonl.gz"
codes=("${PIPESTATUS[@]}")
printf '%s\n' "${codes[*]}" > "$OUTPUT_DIR/pipeline_exit_codes.txt"
if [[ "${codes[*]}" != "0 0" ]]; then
  printf 'failed_pass5_final_local_errors\n' > "$RUN_DIR/state.txt"
  exit 1
fi
python3 "$ROOT/scripts/extract_failed_dns.py" \
  --input "$OUTPUT_DIR/results.jsonl.gz" \
  --output "$OUTPUT_DIR/remaining-failed-input.csv" \
  --summary "$OUTPUT_DIR/remaining-failed-summary.json" \
  > "$OUTPUT_DIR/remaining-extractor.log" || exit 1
gzip -t "$OUTPUT_DIR/results.jsonl.gz"
sha256sum "$OUTPUT_DIR/results.jsonl.gz" > "$OUTPUT_DIR/results.sha256"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/finished_at.txt"
printf 'completed_pass5_final_local_errors\n' > "$RUN_DIR/state.txt"
