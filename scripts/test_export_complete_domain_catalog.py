#!/usr/bin/env python3
"""Tests for universal candidate/DNS/HTTP coverage rows."""

import csv
import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path

from export_complete_domain_catalog import CATALOG_COLUMNS, iter_catalog_rows


class CompleteDomainCatalogTest(unittest.TestCase):
    def test_unavailable_and_unmeasured_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            domains = work / "domains.csv.gz"
            with gzip.open(domains, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain", "unicode_domain", "first_seen_at", "last_seen_at", "source_count", "sources"))
                writer.writerow(("a.ro", "a.ro", "2026-01-01", "2026-01-01", 1, "all_domains_ct"))
                writer.writerow(("b.ro", "b.ro", "2026-01-01", "2026-01-01", 1, "all_domains_ct"))

            dns = work / "dns.csv.gz"
            with gzip.open(dns, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain", "query_type", "status", "queried_at", "duration_ms", "resolver", "protocol", "source_pass", "attempt_sequence", "error_class", "authoritative", "authenticated", "recursion_available", "truncated", "record_count"))
                writer.writerow(("a.ro", "NS", "NOERROR", "2026-08-13", "1", "resolver", "udp", "pass1", "1", "", "0", "0", "1", "0", "2"))
                writer.writerow(("b.ro", "NS", "NXDOMAIN", "2026-08-13", "1", "resolver", "udp", "pass1", "1", "", "0", "0", "1", "0", "0"))

            database = work / "http.sqlite3"
            con = sqlite3.connect(database)
            con.execute(
                "CREATE TABLE sites(domain TEXT PRIMARY KEY,stratum TEXT,status TEXT,origin_url TEXT,final_url TEXT,started_at TEXT,finished_at TEXT,error TEXT,fetch_count INTEGER,page_count INTEGER,sitemap_count INTEGER,sitemap_url_count INTEGER,discovered_url_count INTEGER)"
            )
            con.execute(
                "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("a.ro", "test", "no_origin", None, None, "2026-08-13", "2026-08-19", "all_origin_probes_failed", 4, 0, 0, 0, 0),
            )
            con.commit()
            con.close()

            rows = list(iter_catalog_rows(domains, {"c.ro"}, dns, database))
            result = {row[0]: dict(zip(CATALOG_COLUMNS, row)) for row in rows}
            self.assertEqual(set(result), {"a.ro", "b.ro", "c.ro"})
            self.assertEqual(result["a.ro"]["http_classification"], "unavailable_at_measurement")
            self.assertEqual(result["a.ro"]["dns_delegation_class"], "delegated")
            self.assertEqual(result["b.ro"]["http_classification"], "not_measured_http")
            self.assertEqual(result["b.ro"]["dns_delegation_class"], "nxdomain")
            self.assertEqual(result["c.ro"]["http_classification"], "not_measured_http")
            self.assertEqual(result["c.ro"]["dns_delegation_class"], "not_measured_dns")
            self.assertEqual(result["c.ro"]["in_scrape_the_world_stage2"], 1)


if __name__ == "__main__":
    unittest.main()
