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
) -> tuple[int, int, str, int, int]:
    con.execute("CREATE TEMP TABLE public_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    con.execute(
        "CREATE TEMP TABLE domain_provenance("
        "domain TEXT PRIMARY KEY, discovery_sources TEXT NOT NULL) WITHOUT ROWID"
    )
    base_count = 0
    stw_attributed = 0
    base_domains: set[str] = set()
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
            base_domains.add(domain)
            batch.append((domain, ",".join(sorted(sources))))
            if len(batch) >= 10_000:
                con.executemany("INSERT INTO domain_provenance VALUES (?,?)", batch)
                con.executemany("INSERT INTO public_domains VALUES (?)", ((row[0],) for row in batch))
                base_count += len(batch)
                batch.clear()
        if batch:
            con.executemany("INSERT INTO domain_provenance VALUES (?,?)", batch)
            con.executemany("INSERT INTO public_domains VALUES (?)", ((row[0],) for row in batch))
            base_count += len(batch)

    stw_only = sorted(stw_domains - base_domains)
    con.executemany(
        "INSERT INTO domain_provenance VALUES (?,?)",
        ((domain, STW_SOURCE_ID) for domain in stw_only),
    )
    con.executemany(
        "INSERT INTO public_domains VALUES (?)", ((domain,) for domain in stw_only)
    )
    return (
        base_count,
        base_count + len(stw_only),
        sha256(path),
        stw_attributed,
        len(stw_only),
    )


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
    parser.add_argument("--dns-consensus-summary", type=Path, required=True)
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

    dns_summary_path = args.dns_consensus_summary.resolve()
    dns_summary = json.loads(dns_summary_path.read_text(encoding="utf-8"))
    phases = dns_summary.get("phases")
    if phases:
        valid_dns_summary = (
            dns_summary.get("queue_domains", 0) > 0
            and dns_summary.get("consensus_nxdomain", 0) > 0
            and dns_summary.get("dns_unresolved", 0) > 0
            and dns_summary.get("dns_final_dispositions")
            == dns_summary.get("consensus_nxdomain", 0)
            + dns_summary.get("dns_unresolved", 0)
            and all(
                phase.get("queue_domains", 0) > 0
                and len(phase.get("results", [])) >= 2
                and all(
                    result.get("rows") == phase["queue_domains"]
                    for result in phase["results"]
                )
                and all(
                    shard.get("quick_check") == "ok"
                    for shard in phase.get("shards", [])
                )
                for phase in phases
            )
        )
        public_dns_summary = dns_summary
    else:
        valid_dns_summary = (
            dns_summary.get("queue_domains", 0) > 0
            and dns_summary.get("consensus_nxdomain", 0) > 0
            and len(dns_summary.get("results", [])) >= 2
            and all(
                result.get("rows") == dns_summary["queue_domains"]
                for result in dns_summary["results"]
            )
            and all(
                shard.get("quick_check") == "ok"
                for shard in dns_summary.get("shards", [])
            )
        )
        public_dns_summary = {
            "generated_at": dns_summary["generated_at"],
            "method": dns_summary["method"],
            "queue_domains": dns_summary["queue_domains"],
            "consensus_nxdomain": dns_summary["consensus_nxdomain"],
            "results": [
                {
                    key: result[key]
                    for key in ("bytes", "sha256", "rows", "statuses", "resolvers")
                }
                for result in dns_summary["results"]
            ],
            "shards": [
                {
                    key: shard[key]
                    for key in (
                        "shard", "consensus_domains", "inserted", "replaced_retryable",
                        "preserved_existing_http", "sites_after", "quick_check",
                    )
                }
                for shard in dns_summary["shards"]
            ],
        }
    if not valid_dns_summary:
        raise ValueError("DNS consensus summary is incomplete or invalid")
    public_dns_path = output_dir / "dns_nxdomain_consensus_summary.json"
    public_dns_path.write_text(
        json.dumps(public_dns_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    public_dns_report = {
        "rows": dns_summary.get(
            "dns_final_dispositions", dns_summary["consensus_nxdomain"]
        ),
        "bytes": public_dns_path.stat().st_size,
        "sha256": sha256(public_dns_path),
        "format": "JSON",
    }

    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA temp_store=MEMORY")
    (
        base_scope_rows,
        scope_rows,
        scope_hash,
        stw_scope_domains,
        stw_only_scope_domains,
    ) = load_scope(
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
        "snapshot_window": {"started_at_min": "2026-08-13", "finished_at_max": "2026-08-21"},
        "scope": "complete privacy-minimized projection of every candidate domain in the base plus Scrape The World union",
        "measurement_source": "independent HTTP crawl plus fresh multi-resolver NXDOMAIN consensus",
        "source_database": {
            "bytes": source_summary["database_bytes"],
            "sha256": source_summary["database_sha256"],
            "quick_check": source_summary["quick_check"],
            "expected_domains": source_summary["expected_domains"],
        },
        "candidate_domain_input": {
            "rows": scope_rows,
            "base_rows": base_scope_rows,
            "scrape_the_world_only_rows": stw_only_scope_domains,
            "sha256": scope_hash,
            "source_semantics": "per-domain discovery_sources retain actual provenance; Scrape The World-only domains are appended to the base candidate scope",
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
                "domains_in_candidate_scope": stw_scope_domains + stw_only_scope_domains,
                "domains_already_in_base_scope": stw_scope_domains,
                "domains_added_beyond_base_scope": stw_only_scope_domains,
                "attribution_rule": "applied only to domains present in the supplied stage-two file",
            }
        },
        "coverage": coverage,
        "dns_nxdomain_consensus": public_dns_summary,
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
            "dns_nxdomain_consensus_summary.json": public_dns_report,
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
