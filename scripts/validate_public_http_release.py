#!/usr/bin/env python3
"""Validate hashes, schemas, privacy rules and referential integrity of an HTTP release."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_public_http import (  # noqa: E402
    EVIDENCE_COLUMNS,
    SITE_COLUMNS,
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


def load_public_domains(path: Path) -> set[str]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return {row["domain"] for row in csv.DictReader(handle)}


def safe_url(value: str) -> bool:
    if not value:
        return True
    if sanitize_url(value) != value or EMAIL_RE.search(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.query and not parsed.fragment


def integer(value: str, minimum: int = 0, maximum: int | None = None) -> bool:
    if not value.isdigit():
        return False
    number = int(value)
    return number >= minimum and (maximum is None or number <= maximum)


def validate_csv(
    path: Path,
    expected_columns: tuple[str, ...],
    public_domains: set[str],
    active_cuis: set[str],
) -> tuple[int, list[str], dict[str, int]]:
    errors: list[str] = []
    rows = 0
    counts: dict[str, int] = {}
    previous_domain = ""
    seen_in_domain: set[tuple[str, ...]] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != expected_columns:
            return 0, [f"{path.name}: unexpected header {reader.fieldnames}"], counts
        for line, row in enumerate(reader, 2):
            rows += 1
            domain = row["domain"]
            if domain not in public_domains:
                errors.append(f"{path.name}:{line}: domain is not in the public provenance set")
            if domain < previous_domain:
                errors.append(f"{path.name}:{line}: domains are not sorted")
            if domain != previous_domain:
                previous_domain = domain
                seen_in_domain.clear()
            if EMAIL_RE.search(" ".join(row.values())):
                errors.append(f"{path.name}:{line}: email-like value leaked")

            if path.name == "http_sites_public.csv.gz":
                key = (domain,)
                if not safe_url(row["origin_url"]) or not safe_url(row["final_url"]):
                    errors.append(f"{path.name}:{line}: unsafe URL")
                for field in ("fetch_count", "page_count", "sitemap_count", "sitemap_url_count", "discovered_url_count"):
                    if not integer(row[field]):
                        errors.append(f"{path.name}:{line}: invalid {field}")
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            elif path.name == "sitemaps_public.csv.gz":
                key = tuple(row[column] for column in expected_columns)
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
            else:
                key = (domain, row["cui"], row["final_url"])
                if valid_cui(row["cui"]) != row["cui"] or row["cui"] not in active_cuis:
                    errors.append(f"{path.name}:{line}: invalid or inactive CUI")
                if not safe_url(row["source_url"]) or not safe_url(row["final_url"]):
                    errors.append(f"{path.name}:{line}: unsafe URL")
                if not integer(row["http_status"], 200, 399):
                    errors.append(f"{path.name}:{line}: invalid evidence HTTP status")
                if row["discovery_source"] not in DISCOVERY_SOURCES:
                    errors.append(f"{path.name}:{line}: invalid discovery source")
                if not integer(row["score"], 0, 100):
                    errors.append(f"{path.name}:{line}: invalid score")
                if not HEX64_RE.fullmatch(row["text_sha256"]):
                    errors.append(f"{path.name}:{line}: invalid text hash")

            if key in seen_in_domain:
                errors.append(f"{path.name}:{line}: duplicate public row")
            seen_in_domain.add(key)
            if len(errors) >= 100:
                break
    return rows, errors, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--public-domains", type=Path, required=True)
    parser.add_argument("--companies-dir", type=Path, required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    public_domains = load_public_domains(args.public_domains.resolve())
    active_cuis = load_active_cuis(args.companies_dir.resolve())
    expected = {
        "http_sites_public.csv.gz": SITE_COLUMNS,
        "sitemaps_public.csv.gz": SITEMAP_COLUMNS,
        "company_evidence_public.csv.gz": EVIDENCE_COLUMNS,
    }
    errors: list[str] = []
    results: dict[str, object] = {}
    for name, columns in expected.items():
        path = release_dir / name
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        report = manifest["files"].get(name, {})
        if path.stat().st_size != report.get("bytes"):
            errors.append(f"{name}: byte size differs from manifest")
        digest = sha256(path)
        if digest != report.get("sha256"):
            errors.append(f"{name}: SHA-256 differs from manifest")
        rows, file_errors, counts = validate_csv(path, columns, public_domains, active_cuis)
        errors.extend(file_errors)
        if rows != report.get("rows"):
            errors.append(f"{name}: row count differs from manifest")
        if list(columns) != report.get("columns"):
            errors.append(f"{name}: manifest columns differ from schema")
        results[name] = {"rows": rows, "bytes": path.stat().st_size, "sha256": digest, "counts": counts}

    summary = {
        "release": manifest.get("dataset_version"),
        "public_domains": len(public_domains),
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
