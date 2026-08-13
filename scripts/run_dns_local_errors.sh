#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_RUN="${1:?parent DNS run ID is required}"
RUN_DIR="$ROOT/data/dns/$PARENT_RUN"
SOURCE="$RUN_DIR/pass3/results.jsonl.gz"
OUTPUT_DIR="$RUN_DIR/pass4-local-errors"
INPUT="$OUTPUT_DIR/input.csv"
RESOLVERS="1.1.1.1,8.8.8.8,9.9.9.9"

mkdir -p "$OUTPUT_DIR"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/started_at.txt"
printf '%s\n' "$$" > "$OUTPUT_DIR/pid.txt"
printf 'preparing_pass4_local_errors\n' > "$RUN_DIR/state.txt"
trap 'printf "interrupted_pass4_local_errors\n" > "$RUN_DIR/state.txt"' INT TERM

python3 "$ROOT/scripts/extract_dns_status.py" \
  --input "$SOURCE" --output "$INPUT" \
  --summary "$OUTPUT_DIR/input-summary.json" --statuses ERROR \
  > "$OUTPUT_DIR/extractor.log"
extract_code=$?
if [[ $extract_code -ne 0 || ! -s "$INPUT" ]]; then
  printf 'failed_prepare_pass4_local_errors\n' > "$RUN_DIR/state.txt"
  exit 1
fi

cat > "$OUTPUT_DIR/config.txt" <<EOF
input=$INPUT
input_domains=$(wc -l < "$INPUT")
selected_queries=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_queries"])' "$OUTPUT_DIR/input-summary.json")
threads=50
retries=1
network_timeout_seconds=5
domain_timeout_seconds=30
name_servers=$RESOLVERS
per_resolver_qps=300
EOF

printf 'running_pass4_local_errors\n' > "$RUN_DIR/state.txt"
set +e
zdns MULTIPLE \
  --multi-config-file="$ROOT/scripts/zdns-12-types.ini" \
  --threads=50 --retries=1 --network-timeout=5 --timeout=30 \
  --no-follow-cnames --name-servers="$RESOLVERS" \
  --per-ip-ns-rate-limit=300 \
  --include-fields=protocol,resolver,ttl,flags,dnssec \
  --status-updates-file="$OUTPUT_DIR/status.log" \
  --metadata-file="$OUTPUT_DIR/metadata.json" \
  --input-file="$INPUT" --output-file=- \
  | gzip -1 > "$OUTPUT_DIR/results.jsonl.gz"
scan_codes=("${PIPESTATUS[@]}")
printf '%s\n' "${scan_codes[*]}" > "$OUTPUT_DIR/pipeline_exit_codes.txt"
if [[ "${scan_codes[*]}" != "0 0" ]]; then
  printf 'failed_pass4_local_errors\n' > "$RUN_DIR/state.txt"
  exit 1
fi

python3 "$ROOT/scripts/extract_failed_dns.py" \
  --input "$OUTPUT_DIR/results.jsonl.gz" \
  --output "$OUTPUT_DIR/remaining-failed-input.csv" \
  --summary "$OUTPUT_DIR/remaining-failed-summary.json" \
  > "$OUTPUT_DIR/remaining-extractor.log"
final_code=$?
set -e
if [[ $final_code -ne 0 ]]; then
  printf 'failed_extract_pass4_local_errors\n' > "$RUN_DIR/state.txt"
  exit 1
fi

gzip -t "$OUTPUT_DIR/results.jsonl.gz"
sha256sum "$OUTPUT_DIR/results.jsonl.gz" > "$OUTPUT_DIR/results.sha256"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/finished_at.txt"
printf 'completed_pass4_local_errors\n' > "$RUN_DIR/state.txt"
