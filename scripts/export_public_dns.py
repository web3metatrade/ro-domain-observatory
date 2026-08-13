#!/usr/bin/env python3
"""Export consolidated DNS rows only for publicly redistributable domains."""

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path


QUERY_COLUMNS = (
    "domain", "query_type", "status", "queried_at", "duration_ms", "resolver",
    "protocol", "source_pass", "attempt_sequence", "error_class",
    "authoritative", "authenticated", "recursion_available", "truncated",
    "record_count",
)
RECORD_COLUMNS = (
    "domain", "query_type", "section", "owner_name", "record_type", "ttl",
    "value", "preference", "priority", "weight", "port", "flags", "tag",
    "algorithm", "key_tag", "digest_type", "serial", "refresh", "retry",
    "expire", "minimum_ttl", "rdata_json", "source_pass",
)


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export(con, table, columns, output):
    query = f"""
        SELECT {','.join('d.' + column for column in columns)}
        FROM dns.{table} d JOIN public_domains p ON p.domain=d.domain
        ORDER BY d.domain,d.query_type
    """
    rows = 0
    with gzip.open(output, "wt", encoding="utf-8", newline="", compresslevel=9) as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in con.execute(query):
            writer.writerow(row)
            rows += 1
    return {"rows": rows, "bytes": output.stat().st_size, "sha256": sha256(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-db", required=True)
    parser.add_argument("--dns-db", required=True)
    parser.add_argument("--domain-export", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH DATABASE ? AS registry", (str(Path(args.registry_db).resolve()),))
    con.execute("ATTACH DATABASE ? AS dns", (str(Path(args.dns_db).resolve()),))
    con.execute("CREATE TEMP TABLE public_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    con.execute("""
        INSERT INTO public_domains
        SELECT DISTINCT ds.domain FROM registry.domain_sources ds
        JOIN registry.sources s USING(source_id) WHERE s.public_export=1
    """)
    domain_count = con.execute("SELECT count(*) FROM public_domains").fetchone()[0]
    domain_target = output_dir / "domains_ro_public.csv.gz"
    domain_target.write_bytes(Path(args.domain_export).resolve().read_bytes())
    report = {
        "public_domains": domain_count,
        "domains_ro_public.csv.gz": {
            "rows": domain_count,
            "bytes": domain_target.stat().st_size,
            "sha256": sha256(domain_target),
        },
    }
    report["dns_query_results_public.csv.gz"] = export(
        con, "query_results", QUERY_COLUMNS, output_dir / "dns_query_results_public.csv.gz"
    )
    report["dns_records_public.csv.gz"] = export(
        con, "dns_records", RECORD_COLUMNS, output_dir / "dns_records_public.csv.gz"
    )
    con.close()
    (output_dir / "manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
