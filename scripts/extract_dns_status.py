#!/usr/bin/env python3
"""Create a triggered ZDNS MULTIPLE input for selected result statuses."""

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path


TRIGGERS = {
    "A": "retry-a", "AAAA": "retry-aaaa", "CNAME": "retry-cname",
    "MX": "retry-mx", "NS": "retry-ns", "SOA": "retry-soa",
    "TXT": "retry-txt", "CAA": "retry-caa", "HTTPS": "retry-https",
    "SVCB": "retry-svcb", "DS": "retry-ds", "DNSKEY": "retry-dnskey",
}

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--summary", required=True)
parser.add_argument("--statuses", required=True, help="comma-separated statuses, e.g. ERROR,TIMEOUT")
args = parser.parse_args()

wanted = {value.strip().upper() for value in args.statuses.split(",") if value.strip()}
counts = Counter()
rows = selected_domains = selected_queries = invalid_json = 0

with gzip.open(args.input, "rt", encoding="utf-8", errors="replace") as source, Path(args.output).open(
    "w", encoding="utf-8", newline="\n"
) as target:
    for line in source:
        rows += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            invalid_json += 1
            continue
        triggers = []
        for record_type, result in record.get("results", {}).items():
            status = str(result.get("status", "MISSING")).upper()
            if status in wanted and record_type in TRIGGERS:
                triggers.append(TRIGGERS[record_type])
                counts[record_type] += 1
        if triggers:
            selected_domains += 1
            selected_queries += len(triggers)
            target.write(f"{record['name']},,{','.join(triggers)}\n")

summary = {
    "input": args.input,
    "source_rows": rows,
    "selected_statuses": sorted(wanted),
    "selected_domains": selected_domains,
    "selected_queries": selected_queries,
    "selected_type_counts": dict(sorted(counts.items())),
    "invalid_json_lines": invalid_json,
}
Path(args.summary).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
