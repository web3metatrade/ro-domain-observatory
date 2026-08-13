#!/usr/bin/env python3
"""Create a ZDNS MULTIPLE retry queue from failed module results."""

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path


SUCCESS = {"NOERROR", "NXDOMAIN"}
TRIGGERS = {
    "A": "retry-a",
    "AAAA": "retry-aaaa",
    "CNAME": "retry-cname",
    "MX": "retry-mx",
    "NS": "retry-ns",
    "SOA": "retry-soa",
    "TXT": "retry-txt",
    "CAA": "retry-caa",
    "HTTPS": "retry-https",
    "SVCB": "retry-svcb",
    "DS": "retry-ds",
    "DNSKEY": "retry-dnskey",
}

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--summary", required=True)
args = parser.parse_args()

input_path = Path(args.input)
output_path = Path(args.output)
summary_path = Path(args.summary)
status_counts = Counter()
failed_type_counts = Counter()
rows = failed_rows = invalid_json = 0

with gzip.open(input_path, "rt", encoding="utf-8", errors="replace") as source, output_path.open(
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
            status_counts[status] += 1
            if status not in SUCCESS and record_type in TRIGGERS:
                triggers.append(TRIGGERS[record_type])
                failed_type_counts[record_type] += 1
        if triggers:
            failed_rows += 1
            target.write(f"{record['name']},,{','.join(triggers)}\n")

summary = {
    "input": str(input_path),
    "rows": rows,
    "domains_with_failed_types": failed_rows,
    "failed_type_queries": sum(failed_type_counts.values()),
    "invalid_json_lines": invalid_json,
    "status_counts": dict(sorted(status_counts.items())),
    "failed_type_counts": dict(sorted(failed_type_counts.items())),
}
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
