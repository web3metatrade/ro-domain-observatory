#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${1:-dns-12types-1000qps-retry1-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="$ROOT/data/dns/$RUN_ID"
PASS1="$RUN_DIR/pass1"
PASS2="$RUN_DIR/pass2"
INPUT="$ROOT/data/exports/domains_ro_all_sources.txt.gz"
TOTAL_DOMAINS=746909
TYPE_COUNT=12
TARGET_QPS=1000
PER_RESOLVER_QPS="333.3333333333"
RESOLVERS="1.1.1.1,8.8.8.8,9.9.9.9"

mkdir -p "$PASS1" "$PASS2"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_DIR/started_at.txt"
printf '%s\n' "$$" > "$RUN_DIR/pid.txt"
printf 'running_pass1\n' > "$RUN_DIR/state.txt"
trap 'printf "interrupted\n" > "$RUN_DIR/state.txt"' INT TERM

cat > "$RUN_DIR/config.txt" <<EOF
run_id=$RUN_ID
input=$INPUT
domains=$TOTAL_DOMAINS
types=A,AAAA,CNAME,MX,NS,SOA,TXT,CAA,HTTPS,SVCB,DS,DNSKEY
type_count=$TYPE_COUNT
base_logical_queries=$((TOTAL_DOMAINS * TYPE_COUNT))
target_wire_qps=$TARGET_QPS
per_resolver_qps=$PER_RESOLVER_QPS
threads=1000
retries=1
failed_sweep=true
failed_sweep_retries=1
network_timeout_seconds=2
domain_timeout_seconds=7
name_servers=$RESOLVERS
zdns_version=$(zdns --version 2>&1)
EOF

run_zdns() {
  local status_file="$1"
  local metadata_file="$2"
  zdns MULTIPLE \
    --multi-config-file="$ROOT/scripts/zdns-12-types.ini" \
    --threads=1000 \
    --retries=1 \
    --network-timeout=2 \
    --timeout=7 \
    --no-follow-cnames \
    --name-servers="$RESOLVERS" \
    --per-ip-ns-rate-limit="$PER_RESOLVER_QPS" \
    --include-fields=protocol,resolver,ttl,flags,dnssec \
    --status-updates-file="$status_file" \
    --metadata-file="$metadata_file" \
    --output-file=-
}

set +e
gzip -dc "$INPUT" \
  | run_zdns "$PASS1/status.log" "$PASS1/metadata.json" \
  | gzip -1 > "$PASS1/results.jsonl.gz"
pass1_codes=("${PIPESTATUS[@]}")
printf '%s\n' "${pass1_codes[*]}" > "$PASS1/pipeline_exit_codes.txt"
if [[ "${pass1_codes[*]}" != "0 0 0" ]]; then
  printf 'failed_pass1\n' > "$RUN_DIR/state.txt"
  exit 1
fi

python3 "$ROOT/scripts/extract_failed_dns.py" \
  --input "$PASS1/results.jsonl.gz" \
  --output "$PASS2/failed-input.csv" \
  --summary "$PASS2/failed-summary.json" \
  > "$PASS2/extractor.log"
extract_code=$?
if [[ $extract_code -ne 0 ]]; then
  printf 'failed_extract\n' > "$RUN_DIR/state.txt"
  exit 1
fi

if [[ ! -s "$PASS2/failed-input.csv" ]]; then
  printf 'completed_no_failures\n' > "$RUN_DIR/state.txt"
  printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_DIR/finished_at.txt"
  exit 0
fi

printf 'running_failed_sweep\n' > "$RUN_DIR/state.txt"
run_zdns "$PASS2/status.log" "$PASS2/metadata.json" \
  < "$PASS2/failed-input.csv" \
  | gzip -1 > "$PASS2/results.jsonl.gz"
pass2_codes=("${PIPESTATUS[@]}")
printf '%s\n' "${pass2_codes[*]}" > "$PASS2/pipeline_exit_codes.txt"
if [[ "${pass2_codes[*]}" != "0 0" ]]; then
  printf 'failed_pass2\n' > "$RUN_DIR/state.txt"
  exit 1
fi

python3 "$ROOT/scripts/extract_failed_dns.py" \
  --input "$PASS2/results.jsonl.gz" \
  --output "$PASS2/remaining-failed-input.csv" \
  --summary "$PASS2/remaining-failed-summary.json" \
  > "$PASS2/remaining-extractor.log"
final_extract_code=$?
set -e
if [[ $final_extract_code -ne 0 ]]; then
  printf 'failed_final_extract\n' > "$RUN_DIR/state.txt"
  exit 1
fi

printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_DIR/finished_at.txt"
printf 'completed\n' > "$RUN_DIR/state.txt"
