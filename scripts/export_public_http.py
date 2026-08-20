#!/usr/bin/env python3
"""Export a privacy-minimized public HTTP snapshot from the local crawl DB."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SITE_COLUMNS = (
    "domain",
    "stratum",
    "status",
    "origin_url",
    "final_url",
    "started_at",
    "finished_at",
    "error_class",
    "fetch_count",
    "page_count",
    "sitemap_count",
    "sitemap_url_count",
    "discovered_url_count",
)
SITEMAP_COLUMNS = (
    "domain",
    "url",
    "final_url",
    "http_status",
    "kind",
    "url_count",
    "child_count",
    "depth",
    "truncated",
    "error_class",
)
EVIDENCE_COLUMNS = (
    "domain",
    "cui",
    "source_url",
    "final_url",
    "http_status",
    "page_classes",
    "discovery_source",
    "score",
    "text_sha256",
    "fetched_at",
)
PAGE_CLASS_ALLOWLIST = {
    "about",
    "contact",
    "cookie_policy",
    "gdpr",
    "legal",
    "privacy",
    "terms",
}
SITEMAP_KIND_ALLOWLIST = {
    "empty",
    "error",
    "feed",
    "html",
    "index",
    "invalid_xml",
    "rss",
    "text",
    "unavailable",
    "unknown",
    "urlset",
    "xml",
}
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
EMAIL_LIKE_RE = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower().rstrip(".")
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    return value if DOMAIN_RE.fullmatch(value) else ""


def sanitize_url(value: str | None) -> str:
    """Keep only a public HTTP(S) origin and path; drop credentials/query/fragment."""
    if not value or any(ord(char) < 32 for char in value):
        return ""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        if not DOMAIN_RE.fullmatch(host):
            return ""
        port = parsed.port
        netloc = host
        if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
            netloc = f"{host}:{port}"
        path = parsed.path or "/"
        if EMAIL_LIKE_RE.search(path):
            path = "/"
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    except (UnicodeError, ValueError):
        return ""


def error_class(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.casefold()
    exact = {
        "all_origin_probes_failed": "origin_unreachable",
        "disallow_unreachable": "robots_unreachable",
        "disallow_other_status": "robots_other_status",
    }
    if lowered in exact:
        return exact[lowered]
    patterns = (
        (("timeout", "timed out"), "timeout"),
        (("ssl", "tls", "certificate"), "tls_error"),
        (("decompress", "decode", "encoding"), "content_decode_error"),
        (("dns", "getaddrinfo", "name or service"), "dns_error"),
        (("refused",), "connection_refused"),
        (("reset", "connection", "connect"), "connection_error"),
        (("invalid url", "unknown url", "unsupported url"), "invalid_url"),
        (("robots", "disallow"), "robots_error"),
    )
    for needles, label in patterns:
        if any(needle in lowered for needle in needles):
            return label
    return "other_error"


def sitemap_kind(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return normalized if normalized in SITEMAP_KIND_ALLOWLIST else "other"


def valid_cui(value: str) -> str:
    cui = re.sub(r"^RO", "", (value or "").strip().upper()).replace(" ", "")
    if not cui.isdigit() or not 2 <= len(cui) <= 10 or cui.startswith("0"):
        return ""
    body = cui[:-1].zfill(9)
    checksum = sum(int(a) * int(b) for a, b in zip(body, "753217532")) * 10 % 11
    expected = 0 if checksum == 10 else checksum
    return cui if int(cui[-1]) == expected else ""


def load_active_cuis(directory: Path) -> set[str]:
    cuis: set[str] = set()
    paths = sorted(directory.glob("part-*.csv"))
    if len(paths) != 100:
        raise ValueError(f"expected 100 company shards in {directory}, found {len(paths)}")
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                cui = valid_cui(row["cui"])
                if cui:
                    cuis.add(cui)
    return cuis


def load_public_domains(con: sqlite3.Connection, path: Path) -> tuple[int, str]:
    con.execute("CREATE TEMP TABLE public_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID")
    count = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "domain" not in reader.fieldnames:
            raise ValueError("public-domain export has no domain column")
        batch: list[tuple[str]] = []
        for row in reader:
            domain = normalize_domain(row["domain"])
            if not domain or not domain.endswith(".ro"):
                raise ValueError(f"invalid public domain: {row['domain']!r}")
            batch.append((domain,))
            if len(batch) >= 10_000:
                con.executemany("INSERT INTO public_domains(domain) VALUES (?)", batch)
                count += len(batch)
                batch.clear()
        if batch:
            con.executemany("INSERT INTO public_domains(domain) VALUES (?)", batch)
            count += len(batch)
    return count, sha256(path)


class DeterministicGzipCsv:
    def __init__(self, path: Path, columns: tuple[str, ...]):
        self.path = path
        self.columns = columns
        self.raw = None
        self.compressed = None
        self.text = None
        self.writer = None
        self.rows = 0

    def __enter__(self):
        self.raw = self.path.open("wb")
        self.compressed = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, compresslevel=9, mtime=0)
        self.text = io.TextIOWrapper(self.compressed, encoding="utf-8", newline="")
        self.writer = csv.writer(self.text, lineterminator="\n")
        self.writer.writerow(self.columns)
        return self

    def writerow(self, row):
        self.writer.writerow(row)
        self.rows += 1

    def __exit__(self, exc_type, exc, tb):
        self.text.close()
        self.raw.close()
        return False

    def report(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "bytes": self.path.stat().st_size,
            "sha256": sha256(self.path),
            "columns": list(self.columns),
        }


def export_sites(con: sqlite3.Connection, path: Path) -> tuple[dict[str, object], Counter[str]]:
    statuses: Counter[str] = Counter()
    query = """
        SELECT s.* FROM sites s JOIN public_domains p USING(domain)
        ORDER BY s.domain
    """
    with DeterministicGzipCsv(path, SITE_COLUMNS) as output:
        for row in con.execute(query):
            domain = normalize_domain(row["domain"])
            status = row["status"]
            statuses[status] += 1
            output.writerow(
                (
                    domain,
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


def export_sitemaps(con: sqlite3.Connection, path: Path) -> tuple[dict[str, object], Counter[str]]:
    kinds: Counter[str] = Counter()
    query = """
        SELECT s.* FROM sitemaps s JOIN public_domains p USING(domain)
        ORDER BY s.domain,s.sitemap_id
    """
    current_domain = ""
    seen: set[tuple[object, ...]] = set()
    with DeterministicGzipCsv(path, SITEMAP_COLUMNS) as output:
        for row in con.execute(query):
            domain = normalize_domain(row["domain"])
            if domain != current_domain:
                current_domain = domain
                seen.clear()
            kind = sitemap_kind(row["kind"])
            raw_status = row["status"]
            http_status = raw_status if raw_status is not None and 100 <= raw_status <= 599 else ""
            normalized_error = error_class(row["error"])
            if raw_status is not None and http_status == "" and not normalized_error:
                normalized_error = "invalid_http_status"
            public_row = (
                domain,
                sanitize_url(row["url"]),
                sanitize_url(row["final_url"]),
                http_status,
                kind,
                row["url_count"],
                row["child_count"],
                row["depth"],
                row["truncated"],
                normalized_error,
            )
            if not public_row[1] or public_row in seen:
                continue
            seen.add(public_row)
            kinds[kind] += 1
            output.writerow(public_row)
    return output.report(), kinds


def export_company_evidence(
    con: sqlite3.Connection,
    path: Path,
    active_cuis: set[str],
) -> tuple[dict[str, object], int, int]:
    query = """
        SELECT pg.* FROM pages pg JOIN public_domains p USING(domain)
        WHERE pg.status BETWEEN 200 AND 399
          AND pg.soft_404=0
          AND pg.cuis_json<>'[]'
          AND pg.text_sha256 IS NOT NULL
        ORDER BY pg.domain,pg.page_id
    """
    domains: set[str] = set()
    cuis: set[str] = set()
    current_domain = ""
    seen: set[tuple[str, str, str]] = set()
    with DeterministicGzipCsv(path, EVIDENCE_COLUMNS) as output:
        for row in con.execute(query):
            domain = normalize_domain(row["domain"])
            if domain != current_domain:
                current_domain = domain
                seen.clear()
            try:
                page_cuis = {
                    normalized
                    for value in json.loads(row["cuis_json"])
                    if (normalized := valid_cui(str(value)))
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if len(page_cuis) != 1:
                continue
            cui = next(iter(page_cuis))
            if cui not in active_cuis:
                continue
            source_url = sanitize_url(row["url"])
            final_url = sanitize_url(row["final_url"])
            if not source_url or not final_url:
                continue
            key = (domain, cui, final_url)
            if key in seen:
                continue
            seen.add(key)
            try:
                classes = sorted(
                    PAGE_CLASS_ALLOWLIST.intersection(
                        str(value) for value in json.loads(row["classes_json"])
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                classes = []
            output.writerow(
                (
                    domain,
                    cui,
                    source_url,
                    final_url,
                    row["status"],
                    ";".join(classes),
                    row["source"],
                    row["score"],
                    row["text_sha256"],
                    row["fetched_at"],
                )
            )
            domains.add(domain)
            cuis.add(cui)
    return output.report(), len(domains), len(cuis)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-db", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--public-domains", type=Path, required=True)
    parser.add_argument("--companies-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset-version", default="http-snapshot-2026-08-19-public")
    args = parser.parse_args()

    database = args.http_db.resolve()
    source_summary = json.loads(args.source_summary.resolve().read_text(encoding="utf-8"))
    if source_summary.get("quick_check") != "ok":
        raise ValueError("source summary does not report quick_check=ok")
    if database.stat().st_size != source_summary.get("database_bytes"):
        raise ValueError("source database size differs from the consolidation summary")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    active_cuis = load_active_cuis(args.companies_dir.resolve())
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA temp_store=MEMORY")
    public_domain_rows, public_domain_sha256 = load_public_domains(con, args.public_domains.resolve())
    con.execute("PRAGMA query_only=ON")

    site_report, site_statuses = export_sites(con, output_dir / "http_sites_public.csv.gz")
    sitemap_report, sitemap_kinds = export_sitemaps(con, output_dir / "sitemaps_public.csv.gz")
    evidence_report, evidence_domains, evidence_cuis = export_company_evidence(
        con,
        output_dir / "company_evidence_public.csv.gz",
        active_cuis,
    )
    con.close()

    manifest = {
        "dataset_version": args.dataset_version,
        "snapshot_window": {
            "started_at_min": "2026-08-13",
            "finished_at_max": "2026-08-19",
        },
        "scope": "domains supported by at least one redistributable provenance source",
        "measurement_source": "independent_http_crawl",
        "source_database": {
            "bytes": source_summary["database_bytes"],
            "sha256": source_summary["database_sha256"],
            "quick_check": source_summary["quick_check"],
            "expected_domains": source_summary["expected_domains"],
        },
        "public_domain_input": {
            "rows": public_domain_rows,
            "sha256": public_domain_sha256,
        },
        "active_cui_reference_count": len(active_cuis),
        "privacy": {
            "urls": "HTTP(S) scheme, hostname, port and path only; credentials, query strings and fragments removed",
            "excluded": [
                "raw HTML",
                "page excerpts and titles",
                "email addresses",
                "telephone numbers",
                "JSON-LD payloads",
                "bulk discovered URLs",
                "raw error messages",
            ],
            "company_evidence": "single valid active Romanian CUI observed on a non-soft-404 2xx/3xx page; candidate evidence, not ownership",
        },
        "files": {
            "http_sites_public.csv.gz": site_report,
            "sitemaps_public.csv.gz": sitemap_report,
            "company_evidence_public.csv.gz": evidence_report,
        },
        "statistics": {
            "site_statuses": dict(sorted(site_statuses.items())),
            "sitemap_kinds": dict(sorted(sitemap_kinds.items())),
            "company_evidence_domains": evidence_domains,
            "company_evidence_distinct_cuis": evidence_cuis,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
