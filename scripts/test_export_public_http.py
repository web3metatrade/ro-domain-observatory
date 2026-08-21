#!/usr/bin/env python3
"""Tests for the privacy-minimized HTTP release exporter."""

import csv
import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_public_http.py"
sys.path.insert(0, str(ROOT / "scripts"))
from export_public_http import error_class


class PublicHttpExportTest(unittest.TestCase):
    def test_dns_exception_with_ssl_context_is_not_mislabeled_tls(self):
        self.assertEqual(
            error_class("ClientConnectorDNSError: Cannot connect ssl:default"),
            "dns_error",
        )

    def test_export_filters_and_sanitizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            database = work / "http.sqlite3"
            con = sqlite3.connect(database)
            con.executescript(
                """
                CREATE TABLE sites (
                    domain TEXT PRIMARY KEY, stratum TEXT, status TEXT NOT NULL,
                    origin_url TEXT, final_url TEXT, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, error TEXT, fetch_count INTEGER NOT NULL,
                    page_count INTEGER NOT NULL, sitemap_count INTEGER NOT NULL,
                    sitemap_url_count INTEGER NOT NULL, discovered_url_count INTEGER NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE sitemaps (
                    sitemap_id INTEGER PRIMARY KEY, domain TEXT NOT NULL, url TEXT NOT NULL,
                    final_url TEXT, status INTEGER, kind TEXT NOT NULL, url_count INTEGER NOT NULL,
                    child_count INTEGER NOT NULL, depth INTEGER NOT NULL, truncated INTEGER NOT NULL,
                    error TEXT
                );
                CREATE TABLE pages (
                    page_id INTEGER PRIMARY KEY, domain TEXT NOT NULL, url TEXT NOT NULL,
                    final_url TEXT, status INTEGER, title TEXT, language TEXT,
                    classes_json TEXT NOT NULL, emails_json TEXT NOT NULL,
                    phones_json TEXT NOT NULL, cuis_json TEXT NOT NULL,
                    jsonld_json TEXT NOT NULL, company_identity_json TEXT NOT NULL,
                    text_sha256 TEXT, excerpt TEXT, source TEXT NOT NULL,
                    score INTEGER NOT NULL, soft_404 INTEGER NOT NULL, fetched_at TEXT NOT NULL
                );
                """
            )
            con.executemany(
                "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("a.ro", "public", "complete", "https://a.ro/?token=secret", "https://social.example/@person@example.ro", "2026-08-13", "2026-08-19", None, 3, 1, 1, 2, 5),
                    ("b.ro", "public", "no_origin", None, None, "2026-08-13", "2026-08-19", "all_origin_probes_failed", 1, 0, 0, 0, 0),
                    ("private.ro", "internal", "complete", "https://private.ro/", "https://private.ro/", "2026-08-13", "2026-08-19", None, 1, 1, 0, 0, 0),
                ],
            )
            con.executemany(
                "INSERT INTO sitemaps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, "a.ro", "https://a.ro/sitemap.xml?key=secret", "https://a.ro/sitemap.xml#x", 999, "urlset", 2, 0, 0, 0, None),
                    (2, "a.ro", "https://a.ro/sitemap.xml?other=secret", "https://a.ro/sitemap.xml#y", 999, "urlset", 2, 0, 0, 0, None),
                    (3, "private.ro", "https://private.ro/sitemap.xml", "https://private.ro/sitemap.xml", 200, "urlset", 1, 0, 0, 0, None),
                ],
            )
            con.executemany(
                "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, "a.ro", "https://a.ro/contact?token=secret", "https://a.ro/contact#staff", 200, "Secret title", "ro", '["contact","unknown"]', '["person@a.ro"]', '["0712345678"]', '["30245207"]', '[]', '{}', "a" * 64, "private excerpt", "homepage_link", 90, 0, "2026-08-19T00:00:00Z"),
                    (2, "a.ro", "https://a.ro/legal", "https://a.ro/legal", 200, "Legal", "ro", '["legal"]', '[]', '[]', '["30245207","5254131"]', '[]', '{}', "b" * 64, "private excerpt", "homepage_link", 70, 0, "2026-08-19T00:00:00Z"),
                    (3, "private.ro", "https://private.ro/contact", "https://private.ro/contact", 200, "Private", "ro", '["contact"]', '[]', '[]', '["30245207"]', '[]', '{}', "c" * 64, "private excerpt", "homepage_link", 90, 0, "2026-08-19T00:00:00Z"),
                ],
            )
            con.commit()
            con.close()

            public_domains = work / "domains.csv.gz"
            with gzip.open(public_domains, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain",))
                writer.writerows((("a.ro",), ("b.ro",)))

            companies = work / "companies"
            companies.mkdir()
            for shard in range(100):
                with (companies / f"part-{shard:02d}.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, lineterminator="\n")
                    writer.writerow(("cui", "company_name", "turnover_ron", "turnover_year", "active_as_of"))
                    if shard == 7:
                        writer.writerow(("30245207", "TEST SRL", "", "", "2026-08-05"))

            summary = work / "summary.json"
            summary.write_text(
                json.dumps(
                    {
                        "quick_check": "ok",
                        "database_bytes": database.stat().st_size,
                        "database_sha256": "test-source",
                        "expected_domains": 3,
                    }
                ),
                encoding="utf-8",
            )
            output = work / "output"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--http-db", str(database),
                    "--source-summary", str(summary),
                    "--public-domains", str(public_domains),
                    "--companies-dir", str(companies),
                    "--output-dir", str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            with gzip.open(output / "http_sites_public.csv.gz", "rt", encoding="utf-8", newline="") as handle:
                sites = list(csv.DictReader(handle))
            self.assertEqual([row["domain"] for row in sites], ["a.ro", "b.ro"])
            self.assertEqual(sites[0]["origin_url"], "https://a.ro/")
            self.assertEqual(sites[0]["final_url"], "https://social.example/")
            self.assertEqual(sites[1]["error_class"], "origin_unreachable")

            with gzip.open(output / "sitemaps_public.csv.gz", "rt", encoding="utf-8", newline="") as handle:
                sitemaps = list(csv.DictReader(handle))
            self.assertEqual(len(sitemaps), 1)
            self.assertEqual(sitemaps[0]["url"], "https://a.ro/sitemap.xml")
            self.assertEqual(sitemaps[0]["http_status"], "")
            self.assertEqual(sitemaps[0]["error_class"], "invalid_http_status")

            with gzip.open(output / "company_evidence_public.csv.gz", "rt", encoding="utf-8", newline="") as handle:
                evidence = list(csv.DictReader(handle))
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["cui"], "30245207")
            self.assertEqual(evidence[0]["source_url"], "https://a.ro/contact")
            serialized = json.dumps(evidence)
            self.assertNotIn("person@a.ro", serialized)
            self.assertNotIn("private excerpt", serialized)
            self.assertNotIn("secret", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
