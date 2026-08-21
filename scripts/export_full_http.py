#!/usr/bin/env python3
"""Export the complete privacy-minimized HTTP measurement with provenance."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
from collections import Counter
from pathlib import Path

from export_public_http import (
    DeterministicGzipCsv,
    SITE_COLUMNS,
    export_company_evidence,
    export_sitemaps,
    error_class,
    load_active_cuis,
    normalize_domain,
    sanitize_url,
    sha256,
)


FULL_SITE_COLUMNS = ("domain", "discovery_sources", *SITE_COLUMNS[1:])
STW_SOURCE_ID = "scrape_the_world_stage2"


def read_domain_set(path: Path) -> set[str]:
    domains: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "domain" not in reader.fieldnames:
            raise ValueError(f"{path} has no domain column")
        for row in reader:
            domain = normalize_domain(row["domain"])
            if domain:
                domains.add(domain)
    return domains


def read_scrape_the_world_domains(path: Path) -> tuple[set[str], int]:
    domains: set[str] = set()
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "domain" not in reader.fieldnames:
            raise ValueError(f"{path} has no domain column")
        for row in reader:
            rows += 1
            domain = normalize_domain(row["domain"])
            if domain and domain.endswith(".ro"):
                domains.add(domain)
    return domains, rows


def load_scope(
    con: sqlite3.Connection,
    path: Path,
    stw_domains: set[str],
) -> tuple[int, str, int]:
    con.execute("CREATE TEMP TABLE public_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    con.execute(
        "CREATE TEMP TABLE domain_provenance("
        "domain TEXT PRIMARY KEY, discovery_sources TEXT NOT NULL) WITHOUT ROWID"
    )
    count = 0
    stw_attributed = 0
    batch: list[tuple[str, str]] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"domain", "sources"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} must contain domain and sources")
        for row in reader:
            domain = normalize_domain(row["domain"])
            if not domain or not domain.endswith(".ro"):
                raise ValueError(f"invalid scope domain: {row['domain']!r}")
            sources = {value.strip() for value in row["sources"].split(",") if value.strip()}
            if domain in stw_domains:
                sources.add(STW_SOURCE_ID)
                stw_attributed += 1
            if not sources:
                raise ValueError(f"domain has no discovery source: {domain}")
            batch.append((domain, ",".join(sorted(sources))))
            if len(batch) >= 10_000:
                con.executemany("INSERT INTO domain_provenance VALUES (?,?)", batch)
                con.executemany("INSERT INTO public_domains VALUES (?)", ((row[0],) for row in batch))
                count += len(batch)
                batch.clear()
        if batch:
            con.executemany("INSERT INTO domain_provenance VALUES (?,?)", batch)
            con.executemany("INSERT INTO public_domains VALUES (?)", ((row[0],) for row in batch))
            count += len(batch)
    return count, sha256(path), stw_attributed


def export_sites(con: sqlite3.Connection, path: Path) -> tuple[dict[str, object], Counter[str]]:
    statuses: Counter[str] = Counter()
    query = """
        SELECT s.*,p.discovery_sources
        FROM sites s JOIN domain_provenance p USING(domain)
        ORDER BY s.domain
    """
    with DeterministicGzipCsv(path, FULL_SITE_COLUMNS) as output:
        for row in con.execute(query):
            domain = normalize_domain(row["domain"])
            status = row["status"]
            statuses[status] += 1
            output.writerow(
                (
                    domain,
                    row["discovery_sources"],
                    row["stratum"] or "",
                    status,
                    sanitize_url(row["origin_url"]),
                    sanitize_url(row["final_url"]),
                    row["started_at"],
                    row["finished_at"],
                    error_class(row["error"]),
                    row["fetch_count"],
                    row["page_count"],
                    row["sitemap_count"],
                    row["sitemap_url_count"],
                    row["discovered_url_count"],
                )
            )
    return output.report(), statuses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-db", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--all-domains", type=Path, required=True)
    parser.add_argument("--previous-public-domains", type=Path, required=True)
    parser.add_argument("--scrape-the-world-stage2", type=Path, required=True)
    parser.add_argument("--companies-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset-version", default="http-snapshot-2026-08-19-full-measurement")
    args = parser.parse_args()

    database = args.http_db.resolve()
    source_summary_path = args.source_summary.resolve()
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    if source_summary.get("quick_check") != "ok":
        raise ValueError("source summary does not report quick_check=ok")
    if database.stat().st_size != source_summary.get("database_bytes"):
        raise ValueError("source database size differs from the consolidation summary")

    stw_path = args.scrape_the_world_stage2.resolve()
    stw_domains, stw_rows = read_scrape_the_world_domains(stw_path)
    previous_public = read_domain_set(args.previous_public_domains.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    active_cuis = load_active_cuis(args.companies_dir.resolve())

    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA temp_store=MEMORY")
    scope_rows, scope_hash, stw_scope_domains = load_scope(
        con, args.all_domains.resolve(), stw_domains
    )
    con.execute("CREATE TEMP TABLE previous_public(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    con.executemany("INSERT INTO previous_public VALUES (?)", ((value,) for value in previous_public))
    con.execute("CREATE TEMP TABLE stw_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    con.executemany("INSERT INTO stw_domains VALUES (?)", ((value,) for value in stw_domains))
    coverage = {
        "http_domains": con.execute("SELECT count(*) FROM sites").fetchone()[0],
        "previous_public_http_domains": con.execute(
            "SELECT count(*) FROM sites JOIN previous_public USING(domain)"
        ).fetchone()[0],
        "previously_excluded_http_domains": con.execute(
            "SELECT count(*) FROM sites LEFT JOIN previous_public USING(domain) "
            "WHERE previous_public.domain IS NULL"
        ).fetchone()[0],
        "scrape_the_world_stage2_http_domains": con.execute(
            "SELECT count(*) FROM sites JOIN stw_domains USING(domain)"
        ).fetchone()[0],
        "scrape_the_world_stage2_previously_excluded_http_domains": con.execute(
            "SELECT count(*) FROM sites JOIN stw_domains USING(domain) "
            "LEFT JOIN previous_public USING(domain) WHERE previous_public.domain IS NULL"
        ).fetchone()[0],
    }
    con.execute("PRAGMA query_only=ON")

    site_report, site_statuses = export_sites(con, output_dir / "http_sites_full.csv.gz")
    sitemap_report, sitemap_kinds = export_sitemaps(con, output_dir / "sitemaps_full.csv.gz")
    evidence_report, evidence_domains, evidence_cuis = export_company_evidence(
        con, output_dir / "company_evidence_full.csv.gz", active_cuis
    )
    con.close()

    manifest = {
        "dataset_version": args.dataset_version,
        "snapshot_window": {"started_at_min": "2026-08-13", "finished_at_max": "2026-08-19"},
        "scope": "complete privacy-minimized projection of the local HTTP measurement",
        "measurement_source": "independent_http_crawl",
        "source_database": {
            "bytes": source_summary["database_bytes"],
            "sha256": source_summary["database_sha256"],
            "quick_check": source_summary["quick_check"],
            "expected_domains": source_summary["expected_domains"],
        },
        "candidate_domain_input": {
            "rows": scope_rows,
            "sha256": scope_hash,
            "source_semantics": "per-domain discovery_sources retain the actual candidate-list provenance",
        },
        "attribution": {
            "scrape_the_world": {
                "source_id": STW_SOURCE_ID,
                "name": "Scrape The World stage-two website candidates",
                "url": "https://www.linkedin.com/company/scrape-the-world/",
                "permission": "direct public-merge permission granted by Florin Badita, CEO, and held by the maintainer",
                "source_file_rows": stw_rows,
                "source_file_sha256": sha256(stw_path),
                "unique_ro_domains": len(stw_domains),
                "domains_in_candidate_scope": stw_scope_domains,
                "attribution_rule": "applied only to domains present in the supplied stage-two file",
            }
        },
        "coverage": coverage,
        "active_cui_reference_count": len(active_cuis),
        "privacy": {
            "urls": "HTTP(S) scheme, hostname, port and path only; credentials, query strings and fragments removed",
            "excluded": [
                "raw HTML", "page excerpts and titles", "email addresses", "telephone numbers",
                "JSON-LD payloads", "bulk discovered URLs", "raw error messages",
            ],
            "company_evidence": "single valid active Romanian CUI observed on a non-soft-404 2xx/3xx page; candidate evidence, not ownership",
        },
        "files": {
            "http_sites_full.csv.gz": site_report,
            "sitemaps_full.csv.gz": sitemap_report,
            "company_evidence_full.csv.gz": evidence_report,
        },
        "statistics": {
            "site_statuses": dict(sorted(site_statuses.items())),
            "sitemap_kinds": dict(sorted(sitemap_kinds.items())),
            "company_evidence_domains": evidence_domains,
            "company_evidence_distinct_cuis": evidence_cuis,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
