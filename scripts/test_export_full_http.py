#!/usr/bin/env python3
"""Tests for complete HTTP-export provenance handling."""

import csv
import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path

from export_full_http import STW_SOURCE_ID, load_scope, read_scrape_the_world_domains
from validate_full_http_release import load_provenance


class FullHttpExportTest(unittest.TestCase):
    def test_scrape_the_world_attribution_is_domain_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            stage2 = work / "stage2.csv"
            with stage2.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain",))
                writer.writerow(("a.ro",))
                writer.writerow(("https://www.a.ro/contact",))
                writer.writerow(("outside.ro",))
                writer.writerow(("invalid value",))
            stw_domains, rows = read_scrape_the_world_domains(stage2)
            self.assertEqual(rows, 4)
            self.assertEqual(stw_domains, {"a.ro", "outside.ro"})

            scope = work / "domains.csv.gz"
            with gzip.open(scope, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain", "sources"))
                writer.writerow(("a.ro", "all_domains_ct"))
                writer.writerow(("b.ro", "commoncrawl_domain_graph_2026_may_jun_jul"))

            con = sqlite3.connect(":memory:")
            base_count, count, _, attributed, stw_only = load_scope(
                con, scope, stw_domains
            )
            rows = dict(con.execute("SELECT domain,discovery_sources FROM domain_provenance"))
            con.close()
            self.assertEqual(base_count, 2)
            self.assertEqual(count, 3)
            self.assertEqual(attributed, 1)
            self.assertEqual(stw_only, 1)
            self.assertEqual(rows["a.ro"], f"all_domains_ct,{STW_SOURCE_ID}")
            self.assertEqual(rows["b.ro"], "commoncrawl_domain_graph_2026_may_jun_jul")
            self.assertEqual(rows["outside.ro"], STW_SOURCE_ID)
            self.assertEqual(load_provenance(scope, stw_domains), rows)


if __name__ == "__main__":
    unittest.main()
