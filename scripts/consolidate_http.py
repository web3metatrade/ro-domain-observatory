#!/usr/bin/env python3
"""Consolidate disjoint HTTP crawl databases into one verified SQLite dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE source_databases (
    source_name TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_bytes INTEGER NOT NULL,
    site_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE source_runs (
    source_name TEXT NOT NULL,
    source_run_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    configuration_json TEXT NOT NULL,
    PRIMARY KEY(source_name, source_run_id)
) WITHOUT ROWID;
CREATE TABLE sites (
    domain TEXT PRIMARY KEY,
    stratum TEXT,
    status TEXT NOT NULL,
    origin_url TEXT,
    final_url TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    error TEXT,
    fetch_count INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    sitemap_count INTEGER NOT NULL,
    sitemap_url_count INTEGER NOT NULL,
    discovered_url_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE site_sources (
    domain TEXT PRIMARY KEY,
    source_name TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE fetches (
    fetch_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    purpose TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    content_type TEXT,
    bytes INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    sha256 TEXT,
    error TEXT,
    redirects_json TEXT NOT NULL,
    truncated INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE sitemaps (
    sitemap_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    kind TEXT NOT NULL,
    url_count INTEGER NOT NULL,
    child_count INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE discovered_urls (
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    classes_json TEXT NOT NULL,
    score INTEGER NOT NULL,
    lastmod TEXT,
    PRIMARY KEY(domain, url, source)
) WITHOUT ROWID;
CREATE TABLE pages (
    page_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    title TEXT,
    language TEXT,
    classes_json TEXT NOT NULL,
    emails_json TEXT NOT NULL,
    phones_json TEXT NOT NULL,
    cuis_json TEXT NOT NULL,
    jsonld_json TEXT NOT NULL,
    company_identity_json TEXT NOT NULL DEFAULT '{}',
    text_sha256 TEXT,
    excerpt TEXT,
    source TEXT NOT NULL,
    score INTEGER NOT NULL,
    soft_404 INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
"""


TABLE_COLUMNS = {
    "sites": (
        "domain", "stratum", "status", "origin_url", "final_url", "started_at",
        "finished_at", "error", "fetch_count", "page_count", "sitemap_count",
        "sitemap_url_count", "discovered_url_count",
    ),
    "fetches": (
        "domain", "purpose", "url", "final_url", "status", "content_type", "bytes",
        "duration_ms", "sha256", "error", "redirects_json", "truncated", "fetched_at",
    ),
    "sitemaps": (
        "domain", "url", "final_url", "status", "kind", "url_count", "child_count",
        "depth", "truncated", "error",
    ),
    "discovered_urls": (
        "domain", "url", "source", "classes_json", "score", "lastmod",
    ),
    "pages": (
        "domain", "url", "final_url", "status", "title", "language", "classes_json",
        "emails_json", "phones_json", "cuis_json", "jsonld_json", "company_identity_json",
        "text_sha256", "excerpt", "source", "score", "soft_404", "fetched_at",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_source(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name or not raw_path:
        raise argparse.ArgumentTypeError("sources must use name=path")
    return name, Path(raw_path).resolve()


def source_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA src.table_info({table})")}


def source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM src.sqlite_master WHERE type='table'"
        )
    }


def import_source(
    connection: sqlite3.Connection, source_name: str, source_path: Path
) -> dict[str, int | str | float]:
    started = time.monotonic()
    # Sources are selected from only; the destination is always a separate file.
    connection.execute("ATTACH DATABASE ? AS src", (str(source_path),))
    try:
        tables = source_tables(connection)
        site_count = connection.execute("SELECT COUNT(*) FROM src.sites").fetchone()[0]
        print(f"{utc_now()} {source_name}: importing {site_count:,} sites", flush=True)
        with connection:
            connection.execute(
                "INSERT INTO source_databases VALUES(?,?,?,?,?)",
                (source_name, str(source_path), source_path.stat().st_size, site_count, utc_now()),
            )
            if "crawl_runs" in tables:
                connection.execute(
                    """
                    INSERT INTO source_runs
                    SELECT ?, run_id, started_at, finished_at, configuration_json
                    FROM src.crawl_runs
                    """,
                    (source_name,),
                )
            elif "source_runs" in tables:
                connection.execute(
                    """
                    INSERT INTO source_runs
                    SELECT ? || ':' || source_name, source_run_id,
                           started_at, finished_at, configuration_json
                    FROM src.source_runs
                    """,
                    (source_name,),
                )
            columns = TABLE_COLUMNS["sites"]
            available = source_columns(connection, "sites")
            selections = [
                column if column in available else "0 AS discovered_url_count"
                for column in columns
            ]
            connection.execute(
                f"INSERT INTO sites({','.join(columns)}) "
                f"SELECT {','.join(selections)} FROM src.sites"
            )
            if "site_sources" in tables:
                connection.execute(
                    """
                    INSERT INTO site_sources(domain,source_name)
                    SELECT domain, ? || ':' || source_name FROM src.site_sources
                    """,
                    (source_name,),
                )
            else:
                connection.execute(
                    "INSERT INTO site_sources(domain,source_name) SELECT domain,? FROM src.sites",
                    (source_name,),
                )

        imported: dict[str, int | str | float] = {
            "source": source_name,
            "sites": site_count,
        }
        for table in ("fetches", "sitemaps", "discovered_urls", "pages"):
            columns = TABLE_COLUMNS[table]
            selections = list(columns)
            if table == "pages":
                available = source_columns(connection, table)
                fallbacks = {
                    "company_identity_json": "'{}' AS company_identity_json",
                    "soft_404": "0 AS soft_404",
                }
                for column, fallback in fallbacks.items():
                    if column not in available:
                        selections[columns.index(column)] = fallback
            before = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            with connection:
                connection.execute(
                    f"INSERT INTO {table}({','.join(columns)}) "
                    f"SELECT {','.join(selections)} FROM src.{table}"
                )
            after = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            imported[table] = after - before
            print(
                f"{utc_now()} {source_name}: {table} rows={after-before:,}",
                flush=True,
            )
        imported["seconds"] = round(time.monotonic() - started, 1)
        return imported
    finally:
        connection.execute("DETACH DATABASE src")


def consolidate(
    output: Path,
    sources: list[tuple[str, Path]],
    expected_domains: int,
    replace: bool = False,
) -> dict[str, object]:
    output = output.resolve()
    for name, path in sources:
        if not path.exists():
            raise FileNotFoundError(f"Missing source {name}: {path}")
    if len({name for name, _ in sources}) != len(sources):
        raise ValueError("Source names must be unique")
    if output.exists():
        if not replace:
            raise FileExistsError(f"Output already exists: {output}")
        output.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{output}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output, timeout=120)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA cache_size=-65536")
    connection.execute("PRAGMA temp_store=FILE")
    connection.executescript(SCHEMA)
    reports: list[dict[str, int | str | float]] = []
    try:
        for source_name, source_path in sources:
            reports.append(import_source(connection, source_name, source_path))

        domain_count = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        if domain_count != expected_domains:
            raise RuntimeError(
                f"Expected {expected_domains:,} domains, consolidated {domain_count:,}"
            )

        connection.executescript(
            """
            CREATE INDEX idx_sites_status ON sites(status);
            CREATE INDEX idx_site_sources_source ON site_sources(source_name);
            CREATE INDEX idx_fetches_domain ON fetches(domain);
            CREATE INDEX idx_fetches_status ON fetches(status);
            CREATE INDEX idx_pages_domain ON pages(domain);
            CREATE INDEX idx_sitemaps_domain ON sitemaps(domain);
            """
        )
        connection.commit()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sites", "fetches", "sitemaps", "discovered_urls", "pages")
        }
        orphans = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} AS child "
                "LEFT JOIN sites ON sites.domain=child.domain WHERE sites.domain IS NULL"
            ).fetchone()[0]
            for table in ("fetches", "sitemaps", "discovered_urls", "pages")
        }
        statuses = dict(
            connection.execute("SELECT status,COUNT(*) FROM sites GROUP BY status")
        )
        source_counts = dict(
            connection.execute(
                "SELECT source_name,COUNT(*) FROM site_sources GROUP BY source_name"
            )
        )
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if quick_check != "ok" or any(orphans.values()):
            raise RuntimeError(
                f"Validation failed: integrity={quick_check} orphans={orphans}"
            )
        connection.execute("ANALYZE")
        connection.execute("PRAGMA optimize")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    summary: dict[str, object] = {
        "generated_at": utc_now(),
        "database": str(output),
        "database_bytes": output.stat().st_size,
        "database_sha256": sha256(output),
        "expected_domains": expected_domains,
        "counts": counts,
        "statuses": statuses,
        "source_counts": source_counts,
        "orphans": orphans,
        "quick_check": quick_check,
        "sources": reports,
        "headers": {table: list(columns) for table, columns in TABLE_COLUMNS.items()},
    }
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", action="append", type=parse_source, required=True)
    parser.add_argument("--expected-domains", type=int, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    summary = consolidate(
        args.output, args.source, args.expected_domains, replace=args.replace
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"consolidation_failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise
