#!/usr/bin/env python3
"""Recompute and validate every row in the complete domain coverage catalog."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from itertools import zip_longest
from pathlib import Path

from export_complete_domain_catalog import CATALOG_COLUMNS, iter_catalog_rows
from export_full_http import read_scrape_the_world_domains
from export_public_http import sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--all-domains", type=Path, required=True)
    parser.add_argument("--scrape-the-world-stage2", type=Path, required=True)
    parser.add_argument("--dns-query-results", type=Path, required=True)
    parser.add_argument("--http-db", type=Path, required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    stw_domains, _ = read_scrape_the_world_domains(args.scrape_the_world_stage2.resolve())
    expected_rows = iter_catalog_rows(
        args.all_domains.resolve(), stw_domains, args.dns_query_results.resolve(), args.http_db.resolve()
    )
    catalog = release_dir / "domain_coverage_complete.csv.gz"
    errors: list[str] = []
    rows = 0
    with gzip.open(catalog, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
        if header != CATALOG_COLUMNS:
            errors.append(f"unexpected header: {header}")
        for line, pair in enumerate(zip_longest(reader, expected_rows), 2):
            actual, expected = pair
            if actual is None or expected is None:
                errors.append(f"row-count mismatch at line {line}")
                break
            rows += 1
            if tuple(actual) != tuple(str(value) for value in expected):
                errors.append(f"row mismatch at line {line}, domain={actual[0] if actual else ''}")
                if len(errors) >= 20:
                    break

    for name, report in manifest["files"].items():
        path = release_dir / name
        if not path.is_file():
            errors.append(f"missing asset: {name}")
            continue
        if path.stat().st_size != report["bytes"] or sha256(path) != report["sha256"]:
            errors.append(f"asset identity mismatch: {name}")
    report = manifest["files"][catalog.name]
    if rows != report["rows"] or rows != manifest["catalog"]["rows"]:
        errors.append("catalog row count differs from manifest")

    result = {"rows": rows, "sha256": sha256(catalog), "errors": len(errors)}
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
