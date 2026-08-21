#!/usr/bin/env python3
"""Validate and hash an existing consolidated HTTP SQLite database."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


TABLES = ("sites", "fetches", "sitemaps", "discovered_urls", "pages")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(database: Path, expected: int, output: Path) -> dict:
    database = database.resolve()
    connection = sqlite3.connect(database, timeout=300)
    try:
        connection.execute("PRAGMA busy_timeout=300000")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        if counts["sites"] != expected:
            raise ValueError(
                f"expected {expected:,} site rows, found {counts['sites']:,}"
            )
        statuses = dict(
            connection.execute("SELECT status,COUNT(*) FROM sites GROUP BY status")
        )
        orphans = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} child LEFT JOIN sites "
                "ON sites.domain=child.domain WHERE sites.domain IS NULL"
            ).fetchone()[0]
            for table in TABLES[1:]
        }
        source_counts = {}
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_sources'"
        ).fetchone():
            source_counts = dict(
                connection.execute(
                    "SELECT source_name,COUNT(*) FROM site_sources GROUP BY source_name"
                )
            )
        headers = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in TABLES
        }
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if quick_check != "ok" or any(orphans.values()):
            raise RuntimeError(
                f"database validation failed: quick_check={quick_check} orphans={orphans}"
            )
    finally:
        connection.close()
    report = {
        "generated_at": utc_now(),
        "database": str(database),
        "database_bytes": database.stat().st_size,
        "database_sha256": sha256(database),
        "expected_domains": expected,
        "counts": counts,
        "statuses": statuses,
        "source_counts": source_counts,
        "orphans": orphans,
        "quick_check": quick_check,
        "headers": headers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-domains", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        summarize(args.database, args.expected_domains, args.output),
        ensure_ascii=False, indent=2, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
