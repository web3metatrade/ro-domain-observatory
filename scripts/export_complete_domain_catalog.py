#!/usr/bin/env python3
"""Build one coverage row for every known domain, including unmeasured domains."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

from export_full_http import STW_SOURCE_ID, read_scrape_the_world_domains
from export_public_http import DeterministicGzipCsv, error_class, normalize_domain, sanitize_url, sha256
from dns_nxdomain_consensus import (
    DNS_ERROR,
    DNS_STRATUM,
    DNS_UNRESOLVED_ERROR,
    DNS_UNRESOLVED_STRATUM,
)


CATALOG_COLUMNS = (
    "domain",
    "unicode_domain",
    "discovery_sources",
    "source_count",
    "first_seen_at",
    "last_seen_at",
    "in_scrape_the_world_stage2",
    "dns_ns_status",
    "dns_ns_record_count",
    "dns_delegation_class",
    "dns_a_status",
    "dns_a_record_count",
    "dns_aaaa_status",
    "dns_aaaa_record_count",
    "dns_observed_at",
    "http_crawl_state",
    "http_status",
    "http_classification",
    "origin_url",
    "final_url",
    "http_started_at",
    "http_finished_at",
    "http_error_class",
    "fetch_count",
    "page_count",
    "sitemap_count",
    "sitemap_url_count",
    "discovered_url_count",
)
DNS_TYPES = {"A", "AAAA", "NS"}
HTTP_CLASSIFICATIONS = {
    "complete": "available_at_measurement",
    "no_origin": "http_origin_unreachable_at_measurement",
    "dns_nxdomain": "dns_nxdomain_at_measurement",
    "robots_blocked": "robots_blocked",
    "content_decode_error": "content_decode_error",
    "dns_unresolved": "dns_unresolved_at_measurement",
}


def unicode_domain(domain: str) -> str:
    try:
        return domain.encode("ascii").decode("idna")
    except UnicodeError:
        return ""


def stage2_only_domains(all_domains: Path, stw_domains: set[str]) -> set[str]:
    remaining = set(stw_domains)
    with gzip.open(all_domains, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            remaining.discard(row["domain"])
    return remaining


def iter_candidate_union(all_domains: Path, stw_domains: set[str]):
    stw_only = iter(sorted(stage2_only_domains(all_domains, stw_domains)))
    extra = next(stw_only, None)
    with gzip.open(all_domains, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            domain = row["domain"]
            while extra is not None and extra < domain:
                yield {
                    "domain": extra,
                    "unicode_domain": unicode_domain(extra),
                    "first_seen_at": "2026-08-20",
                    "last_seen_at": "2026-08-20",
                    "sources": STW_SOURCE_ID,
                }
                extra = next(stw_only, None)
            yield row
        while extra is not None:
            yield {
                "domain": extra,
                "unicode_domain": unicode_domain(extra),
                "first_seen_at": "2026-08-20",
                "last_seen_at": "2026-08-20",
                "sources": STW_SOURCE_ID,
            }
            extra = next(stw_only, None)


def iter_dns(path: Path):
    current = None
    values: dict[str, tuple[str, str, str]] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            domain = row["domain"]
            if current is not None and domain != current:
                yield current, values
                values = {}
            current = domain
            if row["query_type"] in DNS_TYPES:
                values[row["query_type"]] = (
                    row["status"], row["record_count"], row["queried_at"]
                )
        if current is not None:
            yield current, values


def iter_http(database: Path):
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("SELECT * FROM sites ORDER BY domain"):
            yield row["domain"], row
    finally:
        con.close()


def delegation_class(status: str, record_count: str) -> str:
    if not status:
        return "not_measured_dns"
    if status == "NOERROR" and record_count.isdigit() and int(record_count) > 0:
        return "delegated"
    if status == "NOERROR":
        return "no_ns_answer"
    if status == "NXDOMAIN":
        return "nxdomain"
    return status.casefold()


def iter_catalog_rows(
    all_domains: Path,
    stw_domains: set[str],
    dns_queries: Path,
    http_database: Path,
):
    dns_iterator = iter(iter_dns(dns_queries))
    http_iterator = iter(iter_http(http_database))
    dns_item = next(dns_iterator, None)
    http_item = next(http_iterator, None)

    for candidate in iter_candidate_union(all_domains, stw_domains):
        domain = normalize_domain(candidate["domain"])
        while dns_item is not None and dns_item[0] < domain:
            dns_item = next(dns_iterator, None)
        dns = dns_item[1] if dns_item is not None and dns_item[0] == domain else {}
        while http_item is not None and http_item[0] < domain:
            http_item = next(http_iterator, None)
        site = http_item[1] if http_item is not None and http_item[0] == domain else None

        sources = {value.strip() for value in candidate["sources"].split(",") if value.strip()}
        in_stw = domain in stw_domains
        if in_stw:
            sources.add(STW_SOURCE_ID)
        source_list = sorted(sources)
        first_seen = candidate["first_seen_at"]
        last_seen = max(candidate["last_seen_at"], "2026-08-20") if in_stw else candidate["last_seen_at"]
        ns = dns.get("NS", ("", "", ""))
        a = dns.get("A", ("", "", ""))
        aaaa = dns.get("AAAA", ("", "", ""))
        observed = max((value[2] for value in (ns, a, aaaa) if value[2]), default="")

        if site is None:
            http_values = (
                "not_crawled", "", "not_measured_http", "", "", "", "", "", "", "", "", "", "",
            )
        else:
            status = site["status"]
            if (
                status == "dns_nxdomain"
                and site["stratum"] == DNS_STRATUM
                and site["error"] == DNS_ERROR
            ):
                crawl_state = "dns_verified_nxdomain"
            elif (
                status == "dns_unresolved"
                and site["stratum"] == DNS_UNRESOLVED_STRATUM
                and site["error"] == DNS_UNRESOLVED_ERROR
            ):
                crawl_state = "dns_verified_unresolved"
            else:
                crawl_state = "crawled"
            http_values = (
                crawl_state,
                status,
                HTTP_CLASSIFICATIONS.get(status, "other_http_result"),
                sanitize_url(site["origin_url"]),
                sanitize_url(site["final_url"]),
                site["started_at"],
                site["finished_at"],
                error_class(site["error"]),
                site["fetch_count"],
                site["page_count"],
                site["sitemap_count"],
                site["sitemap_url_count"],
                site["discovered_url_count"],
            )

        yield (
            domain,
            candidate.get("unicode_domain") or unicode_domain(domain),
            ",".join(source_list),
            len(source_list),
            first_seen,
            last_seen,
            int(in_stw),
            ns[0], ns[1], delegation_class(ns[0], ns[1]),
            a[0], a[1], aaaa[0], aaaa[1], observed,
            *http_values,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-domains", type=Path, required=True)
    parser.add_argument("--scrape-the-world-stage2", type=Path, required=True)
    parser.add_argument("--dns-query-results", type=Path, required=True)
    parser.add_argument("--http-db", type=Path, required=True)
    parser.add_argument("--full-release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dataset-version", default="http-snapshot-2026-08-21-corrected-dispositions"
    )
    args = parser.parse_args()

    all_domains = args.all_domains.resolve()
    stw_path = args.scrape_the_world_stage2.resolve()
    dns_queries = args.dns_query_results.resolve()
    http_database = args.http_db.resolve()
    old_release = args.full_release_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stw_domains, stw_rows = read_scrape_the_world_domains(stw_path)
    old_manifest = json.loads((old_release / "manifest.json").read_text(encoding="utf-8"))
    for name in old_manifest["files"]:
        shutil.copyfile(old_release / name, output_dir / name)

    classifications: Counter[str] = Counter()
    delegation: Counter[str] = Counter()
    rows = 0
    stw_rows_out = 0
    catalog = output_dir / "domain_coverage_complete.csv.gz"
    with DeterministicGzipCsv(catalog, CATALOG_COLUMNS) as output:
        for row in iter_catalog_rows(all_domains, stw_domains, dns_queries, http_database):
            output.writerow(row)
            rows += 1
            stw_rows_out += int(row[6])
            delegation[row[9]] += 1
            classifications[row[17]] += 1
    catalog_report = output.report()

    manifest = old_manifest
    manifest["dataset_version"] = args.dataset_version
    manifest["scope"] = "all known candidate domains, each with a completed HTTP crawl disposition"
    manifest["files"][catalog.name] = catalog_report
    manifest["catalog"] = {
        "rows": rows,
        "base_candidate_rows": old_manifest["candidate_domain_input"].get(
            "base_rows", old_manifest["candidate_domain_input"]["rows"]
        ),
        "scrape_the_world_source_file_rows": stw_rows,
        "scrape_the_world_unique_ro_domains": len(stw_domains),
        "scrape_the_world_domains_in_catalog": stw_rows_out,
        "new_scrape_the_world_domains_added_to_catalog": rows
        - old_manifest["candidate_domain_input"].get(
            "base_rows", old_manifest["candidate_domain_input"]["rows"]
        ),
        "http_classifications": dict(sorted(classifications.items())),
        "dns_delegation_classes": dict(sorted(delegation.items())),
        "semantics": {
            "unavailable_at_measurement": "HTTP origins were attempted but none was usable during the snapshot",
            "not_measured_http": "the domain remains in the catalog but was not part of this HTTP crawl",
        },
    }
    manifest["attribution"]["scrape_the_world"]["domains_in_complete_catalog"] = stw_rows_out
    manifest["attribution"]["scrape_the_world"]["new_domains_added_to_complete_catalog"] = (
        rows
        - old_manifest["candidate_domain_input"].get(
            "base_rows", old_manifest["candidate_domain_input"]["rows"]
        )
    )
    manifest["inputs"] = {
        "all_domains_sha256": sha256(all_domains),
        "scrape_the_world_stage2_sha256": sha256(stw_path),
        "dns_query_results_sha256": sha256(dns_queries),
        "http_database_sha256": old_manifest["source_database"]["sha256"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["catalog"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
