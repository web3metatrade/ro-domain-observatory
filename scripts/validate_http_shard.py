#!/usr/bin/env python3
"""Checkpoint and validate a completed resumable HTTP crawl shard."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-domains", type=int, required=True)
    args = parser.parse_args()

    connection = sqlite3.connect(args.database.resolve(), timeout=120)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    stored = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    retryable = connection.execute(
        "SELECT COUNT(*) FROM sites "
        "WHERE status IN ('local_network_error','worker_error')"
    ).fetchone()[0]
    quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
    statuses = dict(
        connection.execute("SELECT status,COUNT(*) FROM sites GROUP BY status")
    )
    connection.close()
    report = {
        "database": str(args.database.resolve()),
        "stored": stored,
        "expected": args.expected_domains,
        "retryable": retryable,
        "quick_check": quick_check,
        "statuses": statuses,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return int(
        stored != args.expected_domains or retryable != 0 or quick_check != "ok"
    )


if __name__ == "__main__":
    raise SystemExit(main())
