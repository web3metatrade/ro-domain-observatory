#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_RUN="${1:?parent DNS run ID is required}"
PASS_NAME="${2:-pass3}"
RUN_DIR="$ROOT/data/dns/$PARENT_RUN"
INPUT="$RUN_DIR/pass2/remaining-failed-input.csv"
OUTPUT_DIR="$RUN_DIR/$PASS_NAME"
RESOLVERS="1.1.1.1,8.8.8.8,9.9.9.9"
PER_RESOLVER_QPS="333.3333333333"

if [[ ! -s "$INPUT" ]]; then
  printf 'Retry input does not exist or is empty: %s\n' "$INPUT" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/started_at.txt"
printf '%s\n' "$$" > "$OUTPUT_DIR/pid.txt"
printf 'running_pass3\n' > "$RUN_DIR/state.txt"
trap 'printf "interrupted_pass3\n" > "$RUN_DIR/state.txt"' INT TERM

cat > "$OUTPUT_DIR/config.txt" <<EOF
input=$INPUT
input_domains=$(wc -l < "$INPUT")
target_wire_qps=1000
per_resolver_qps=$PER_RESOLVER_QPS
threads=150
retries=1
network_timeout_seconds=3
domain_timeout_seconds=20
name_servers=$RESOLVERS
zdns_version=$(zdns --version 2>&1)
EOF

set +e
zdns MULTIPLE \
  --multi-config-file="$ROOT/scripts/zdns-12-types.ini" \
  --threads=150 \
  --retries=1 \
  --network-timeout=3 \
  --timeout=20 \
  --no-follow-cnames \
  --name-servers="$RESOLVERS" \
  --per-ip-ns-rate-limit="$PER_RESOLVER_QPS" \
  --include-fields=protocol,resolver,ttl,flags,dnssec \
  --status-updates-file="$OUTPUT_DIR/status.log" \
  --metadata-file="$OUTPUT_DIR/metadata.json" \
  --input-file="$INPUT" \
  --output-file=- \
  | gzip -1 > "$OUTPUT_DIR/results.jsonl.gz"
scan_codes=("${PIPESTATUS[@]}")
printf '%s\n' "${scan_codes[*]}" > "$OUTPUT_DIR/pipeline_exit_codes.txt"
if [[ "${scan_codes[*]}" != "0 0" ]]; then
  printf 'failed_pass3\n' > "$RUN_DIR/state.txt"
  exit 1
fi

python3 "$ROOT/scripts/extract_failed_dns.py" \
  --input "$OUTPUT_DIR/results.jsonl.gz" \
  --output "$OUTPUT_DIR/remaining-failed-input.csv" \
  --summary "$OUTPUT_DIR/remaining-failed-summary.json" \
  > "$OUTPUT_DIR/extractor.log"
extract_code=$?
set -e
if [[ $extract_code -ne 0 ]]; then
  printf 'failed_pass3_extract\n' > "$RUN_DIR/state.txt"
  exit 1
fi

gzip -t "$OUTPUT_DIR/results.jsonl.gz"
sha256sum "$OUTPUT_DIR/results.jsonl.gz" > "$OUTPUT_DIR/results.sha256"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUTPUT_DIR/finished_at.txt"
printf 'completed_pass3\n' > "$RUN_DIR/state.txt"
