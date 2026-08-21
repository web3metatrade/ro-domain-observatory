#!/usr/bin/env python3
"""Validate the complete privacy-minimized HTTP release and its provenance."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from pathlib import Path

from export_full_http import FULL_SITE_COLUMNS, STW_SOURCE_ID, read_scrape_the_world_domains
from export_public_http import (
    EVIDENCE_COLUMNS,
    SITEMAP_COLUMNS,
    SITEMAP_KIND_ALLOWLIST,
    load_active_cuis,
    sanitize_url,
    sha256,
    valid_cui,
)


EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", re.IGNORECASE)
HEX64_RE = re.compile(r"[0-9a-f]{64}")
DISCOVERY_SOURCES = {"homepage", "homepage_link", "route_guess", "sitemap"}


def load_provenance(path: Path, stw_domains: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            domain = row["domain"]
            sources = {value.strip() for value in row["sources"].split(",") if value.strip()}
            if domain in stw_domains:
                sources.add(STW_SOURCE_ID)
            result[domain] = ",".join(sorted(sources))
    return result


def safe_url(value: str) -> bool:
    return not value or (sanitize_url(value) == value and not EMAIL_RE.search(value))


def integer(value: str, minimum: int = 0, maximum: int | None = None) -> bool:
    if not value.isdigit():
        return False
    number = int(value)
    return number >= minimum and (maximum is None or number <= maximum)


def validate_sites(path: Path, provenance: dict[str, str]) -> tuple[int, list[str], dict[str, int]]:
    errors: list[str] = []
    counts: dict[str, int] = {}
    rows = 0
    previous = ""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FULL_SITE_COLUMNS:
            return 0, [f"{path.name}: unexpected header"], counts
        for line, row in enumerate(reader, 2):
            rows += 1
            domain = row["domain"]
            if domain not in provenance:
                errors.append(f"{path.name}:{line}: domain missing from candidate input")
            elif row["discovery_sources"] != provenance[domain]:
                errors.append(f"{path.name}:{line}: discovery provenance mismatch")
            if domain <= previous:
                errors.append(f"{path.name}:{line}: domains are not strictly sorted")
            previous = domain
            if EMAIL_RE.search(" ".join(row.values())):
                errors.append(f"{path.name}:{line}: email-like value leaked")
            if not safe_url(row["origin_url"]) or not safe_url(row["final_url"]):
                errors.append(f"{path.name}:{line}: unsafe URL")
            for field in ("fetch_count", "page_count", "sitemap_count", "sitemap_url_count", "discovered_url_count"):
                if not integer(row[field]):
                    errors.append(f"{path.name}:{line}: invalid {field}")
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            if len(errors) >= 100:
                break
    return rows, errors, counts


def validate_sitemaps(path: Path, provenance: dict[str, str]) -> tuple[int, list[str], dict[str, int]]:
    errors: list[str] = []
    counts: dict[str, int] = {}
    rows = 0
    previous_domain = ""
    previous_key: tuple[str, ...] | None = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SITEMAP_COLUMNS:
            return 0, [f"{path.name}: unexpected header"], counts
        for line, row in enumerate(reader, 2):
            rows += 1
            domain = row["domain"]
            if domain not in provenance:
                errors.append(f"{path.name}:{line}: domain missing from candidate input")
            if domain < previous_domain:
                errors.append(f"{path.name}:{line}: domains are not sorted")
            if domain != previous_domain:
                previous_domain = domain
                previous_key = None
            key = tuple(row[column] for column in SITEMAP_COLUMNS)
            if key == previous_key:
                errors.append(f"{path.name}:{line}: duplicate row")
            previous_key = key
            if EMAIL_RE.search(" ".join(row.values())):
                errors.append(f"{path.name}:{line}: email-like value leaked")
            if not row["url"] or not safe_url(row["url"]) or not safe_url(row["final_url"]):
                errors.append(f"{path.name}:{line}: unsafe URL")
            if row["http_status"] and not integer(row["http_status"], 100, 599):
                errors.append(f"{path.name}:{line}: invalid HTTP status")
            if row["kind"] not in SITEMAP_KIND_ALLOWLIST | {"other"}:
                errors.append(f"{path.name}:{line}: uncontrolled sitemap kind")
            for field in ("url_count", "child_count", "depth", "truncated"):
                if not integer(row[field]):
                    errors.append(f"{path.name}:{line}: invalid {field}")
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
            if len(errors) >= 100:
                break
    return rows, errors, counts


def validate_evidence(path: Path, provenance: dict[str, str], active_cuis: set[str]) -> tuple[int, list[str]]:
    errors: list[str] = []
    rows = 0
    previous_domain = ""
    seen: set[tuple[str, str, str]] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EVIDENCE_COLUMNS:
            return 0, [f"{path.name}: unexpected header"]
        for line, row in enumerate(reader, 2):
            rows += 1
            domain = row["domain"]
            if domain not in provenance:
                errors.append(f"{path.name}:{line}: domain missing from candidate input")
            if domain < previous_domain:
                errors.append(f"{path.name}:{line}: domains are not sorted")
            if domain != previous_domain:
                previous_domain = domain
                seen.clear()
            key = (domain, row["cui"], row["final_url"])
            if key in seen:
                errors.append(f"{path.name}:{line}: duplicate evidence")
            seen.add(key)
            if EMAIL_RE.search(" ".join(row.values())):
                errors.append(f"{path.name}:{line}: email-like value leaked")
            if valid_cui(row["cui"]) != row["cui"] or row["cui"] not in active_cuis:
                errors.append(f"{path.name}:{line}: invalid or inactive CUI")
            if not safe_url(row["source_url"]) or not safe_url(row["final_url"]):
                errors.append(f"{path.name}:{line}: unsafe URL")
            if not integer(row["http_status"], 200, 399):
                errors.append(f"{path.name}:{line}: invalid HTTP status")
            if row["discovery_source"] not in DISCOVERY_SOURCES:
                errors.append(f"{path.name}:{line}: invalid discovery source")
            if not integer(row["score"], 0, 100) or not HEX64_RE.fullmatch(row["text_sha256"]):
                errors.append(f"{path.name}:{line}: invalid score or text hash")
            if len(errors) >= 100:
                break
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--all-domains", type=Path, required=True)
    parser.add_argument("--scrape-the-world-stage2", type=Path, required=True)
    parser.add_argument("--companies-dir", type=Path, required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    stw_domains, _ = read_scrape_the_world_domains(args.scrape_the_world_stage2.resolve())
    provenance = load_provenance(args.all_domains.resolve(), stw_domains)
    active_cuis = load_active_cuis(args.companies_dir.resolve())
    errors: list[str] = []
    results: dict[str, object] = {}

    validators = {
        "http_sites_full.csv.gz": lambda path: validate_sites(path, provenance),
        "sitemaps_full.csv.gz": lambda path: validate_sitemaps(path, provenance),
        "company_evidence_full.csv.gz": lambda path: (*validate_evidence(path, provenance, active_cuis), {}),
    }
    for name, validator in validators.items():
        path = release_dir / name
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        report = manifest["files"].get(name, {})
        digest = sha256(path)
        if path.stat().st_size != report.get("bytes"):
            errors.append(f"{name}: byte size differs from manifest")
        if digest != report.get("sha256"):
            errors.append(f"{name}: SHA-256 differs from manifest")
        rows, file_errors, counts = validator(path)
        errors.extend(file_errors)
        if rows != report.get("rows"):
            errors.append(f"{name}: row count differs from manifest")
        results[name] = {"rows": rows, "bytes": path.stat().st_size, "sha256": digest, "counts": counts}

    if results.get("http_sites_full.csv.gz", {}).get("rows") != manifest["source_database"]["expected_domains"]:
        errors.append("site export does not cover every source crawl domain")
    if manifest["attribution"]["scrape_the_world"]["source_file_sha256"] != sha256(args.scrape_the_world_stage2.resolve()):
        errors.append("Scrape The World source-file hash mismatch")

    summary = {
        "release": manifest.get("dataset_version"),
        "candidate_domains": len(provenance),
        "active_cuis": len(active_cuis),
        "files": results,
        "errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if errors:
        print("\n".join(errors[:100]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
