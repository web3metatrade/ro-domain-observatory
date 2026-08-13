#!/usr/bin/env python3
"""Consolidate ordered ZDNS passes into one final query and record database."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    started_at TEXT,
    completed_at TEXT,
    domain_count INTEGER NOT NULL,
    query_type_count INTEGER NOT NULL,
    expected_queries INTEGER NOT NULL,
    pass_count INTEGER NOT NULL,
    tool TEXT NOT NULL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS query_results (
    domain TEXT NOT NULL,
    query_type TEXT NOT NULL,
    status TEXT NOT NULL,
    queried_at TEXT,
    duration_ms REAL,
    resolver TEXT,
    protocol TEXT,
    source_pass TEXT NOT NULL,
    attempt_sequence INTEGER NOT NULL,
    error_class TEXT,
    authoritative INTEGER,
    authenticated INTEGER,
    recursion_available INTEGER,
    truncated INTEGER,
    record_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(domain, query_type)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS dns_records (
    record_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    query_type TEXT NOT NULL,
    section TEXT NOT NULL,
    owner_name TEXT,
    record_type TEXT NOT NULL,
    ttl INTEGER,
    value TEXT,
    preference INTEGER,
    priority INTEGER,
    weight INTEGER,
    port INTEGER,
    flags INTEGER,
    tag TEXT,
    algorithm INTEGER,
    key_tag INTEGER,
    digest_type INTEGER,
    serial INTEGER,
    refresh INTEGER,
    retry INTEGER,
    expire INTEGER,
    minimum_ttl INTEGER,
    rdata_json TEXT NOT NULL,
    source_pass TEXT NOT NULL
);
"""

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


def classify_error(status: str, error: str | None) -> str | None:
    if status in {"NOERROR", "NXDOMAIN"}:
        return None
    text = (error or "").lower()
    if "rate limit exceeded" in text:
        return "local_rate_limit"
    if "rate limiter" in text:
        return "local_rate_limiter_deadline"
    if status == "TIMEOUT" or "timeout" in text:
        return "timeout"
    if status == "SERVFAIL":
        return "servfail"
    if status == "REFUSED":
        return "refused"
    return "other"


def bool_int(value):
    return None if value is None else int(bool(value))


def record_value(rr: dict) -> str | None:
    if "answer" in rr:
        return str(rr["answer"])
    if rr.get("type") == "CAA":
        return str(rr.get("value", ""))
    if rr.get("type") in {"HTTPS", "SVCB"}:
        return str(rr.get("target", ""))
    if rr.get("type") == "DS":
        return str(rr.get("digest", ""))
    if rr.get("type") == "DNSKEY":
        return str(rr.get("public_key", ""))
    if rr.get("type") == "SOA":
        return str(rr.get("ns", ""))
    return None


def record_row(domain: str, query_type: str, section: str, rr: dict, source_pass: str):
    common = {"name", "type", "class", "ttl"}
    rdata = {key: value for key, value in rr.items() if key not in common}
    return (
        domain, query_type, section, rr.get("name"), str(rr.get("type", "UNKNOWN")),
        rr.get("ttl"), record_value(rr), rr.get("preference"), rr.get("priority"),
        rr.get("weight"), rr.get("port"), rr.get("flags", rr.get("flag")),
        rr.get("tag"), rr.get("algorithm"), rr.get("key_tag"), rr.get("digest_type"),
        rr.get("serial"), rr.get("refresh"), rr.get("retry"), rr.get("expire"),
        rr.get("min_ttl"), json.dumps(rdata, ensure_ascii=False, sort_keys=True), source_pass,
    )


def iter_results(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def import_pass(con: sqlite3.Connection, path: Path, pass_name: str, sequence: int, batch_size: int = 2500):
    query_rows, record_rows, replaced_keys = [], [], []
    names = lookups = records = 0
    statuses: dict[str, int] = {}
    query_sql = f"INSERT OR REPLACE INTO query_results({','.join(QUERY_COLUMNS)}) VALUES({','.join('?' for _ in QUERY_COLUMNS)})"
    record_sql = f"INSERT INTO dns_records({','.join(RECORD_COLUMNS)}) VALUES({','.join('?' for _ in RECORD_COLUMNS)})"

    def flush():
        nonlocal query_rows, record_rows, replaced_keys
        if sequence > 1 and replaced_keys:
            con.executemany("DELETE FROM dns_records WHERE domain=? AND query_type=?", replaced_keys)
        con.executemany(query_sql, query_rows)
        if record_rows:
            con.executemany(record_sql, record_rows)
        con.commit()
        query_rows, record_rows, replaced_keys = [], [], []

    for record in iter_results(path):
        names += 1
        domain = record["name"].rstrip(".").lower()
        for query_type, result in record.get("results", {}).items():
            lookups += 1
            status = str(result.get("status", "MISSING")).upper()
            statuses[status] = statuses.get(status, 0) + 1
            data = result.get("data") or {}
            flags = data.get("flags") or {}
            current_records = []
            for json_key, section in (("answers", "answer"), ("authorities", "authority"), ("additionals", "additional")):
                for rr in data.get(json_key) or []:
                    if rr.get("type") == "EDNS0":
                        continue
                    current_records.append(record_row(domain, query_type, section, rr, pass_name))
            records += len(current_records)
            query_rows.append((
                domain, query_type, status, result.get("timestamp"),
                round(float(result.get("duration", 0)) * 1000, 3) if result.get("duration") is not None else None,
                data.get("resolver"), data.get("protocol"), pass_name, sequence,
                classify_error(status, result.get("error")), bool_int(flags.get("authoritative")),
                bool_int(flags.get("authenticated")), bool_int(flags.get("recursion_available")),
                bool_int(flags.get("truncated")), len(current_records),
            ))
            record_rows.extend(current_records)
            replaced_keys.append((domain, query_type))
            if len(query_rows) >= batch_size:
                flush()
        if names % 100_000 == 0:
            print(f"{pass_name}: {names:,} domains / {lookups:,} query results", file=sys.stderr, flush=True)
    if query_rows:
        flush()
    return {"pass": pass_name, "path": str(path), "domains": names, "queries": lookups, "records": records, "statuses": statuses}


def export_table(con: sqlite3.Connection, table: str, columns: tuple[str, ...], output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(con.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY domain,query_type"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-db", required=True)
    parser.add_argument("--export-dir")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    output_db = Path(args.output_db).resolve()
    passes = [
        ("pass1", run_dir / "pass1/results.jsonl.gz"),
        ("pass2", run_dir / "pass2/results.jsonl.gz"),
        ("pass3", run_dir / "pass3/results.jsonl.gz"),
        ("pass4_local_errors", run_dir / "pass4-local-errors/results.jsonl.gz"),
        ("pass5_final_local_errors", run_dir / "pass5-final-local-errors/results.jsonl.gz"),
    ]
    missing = [str(path) for _, path in passes if not path.exists()]
    if missing:
        raise SystemExit("Missing input files: " + ", ".join(missing))
    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()
    con = sqlite3.connect(output_db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-262144")
    con.executescript(SCHEMA)
    reports = []
    try:
        for sequence, (name, path) in enumerate(passes, 1):
            reports.append(import_pass(con, path, name, sequence))
            if sequence == 1:
                # Later passes replace a subset of query keys. Build this
                # index after the large initial load so those deletes remain
                # indexed without slowing every pass-one insert.
                con.execute("CREATE INDEX idx_dns_records_domain_type ON dns_records(domain,query_type)")
                con.commit()
        config = {}
        for line in (run_dir / "config.txt").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                config[key] = value
        con.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)",
            (run_dir.name, (run_dir / "started_at.txt").read_text().strip(),
             (run_dir / "pass5-final-local-errors/finished_at.txt").read_text().strip(),
             int(config["domains"]), int(config["type_count"]), int(config["base_logical_queries"]),
             len(passes), "ZDNS " + config.get("zdns_version", ""),
             "Later passes replace earlier results for the same domain and query type."),
        )
        con.commit()
        con.executescript("""
            CREATE INDEX idx_query_results_status ON query_results(status);
            CREATE INDEX idx_query_results_type ON query_results(query_type);
            CREATE INDEX idx_dns_records_record_type ON dns_records(record_type);
        """)
        con.execute("ANALYZE")
        con.execute("PRAGMA optimize")
        con.commit()
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        summary = {
            "database": str(output_db),
            "query_header": list(QUERY_COLUMNS),
            "record_header": list(RECORD_COLUMNS),
            "query_results": con.execute("SELECT count(*) FROM query_results").fetchone()[0],
            "dns_records": con.execute("SELECT count(*) FROM dns_records").fetchone()[0],
            "domains": con.execute("SELECT count(DISTINCT domain) FROM query_results").fetchone()[0],
            "statuses": dict(con.execute("SELECT status,count(*) FROM query_results GROUP BY status")),
            "error_classes": dict(con.execute("SELECT coalesce(error_class,'none'),count(*) FROM query_results GROUP BY error_class")),
            "record_types": dict(con.execute("SELECT record_type,count(*) FROM dns_records GROUP BY record_type")),
            "integrity_check": integrity,
            "passes": reports,
        }
        if args.export_dir:
            export_dir = Path(args.export_dir).resolve()
            query_export = export_dir / "dns_query_results.csv.gz"
            record_export = export_dir / "dns_records.csv.gz"
            export_table(con, "query_results", QUERY_COLUMNS, query_export)
            export_table(con, "dns_records", RECORD_COLUMNS, record_export)
            summary["exports"] = {
                str(query_export): {"bytes": query_export.stat().st_size, "sha256": sha256(query_export)},
                str(record_export): {"bytes": record_export.stat().st_size, "sha256": sha256(record_export)},
            }
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    summary["database_bytes"] = output_db.stat().st_size
    summary["database_sha256"] = sha256(output_db)
    report_path = output_db.with_suffix(".summary.json")
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
