#!/usr/bin/env python3
"""Tests for deterministic HTTP verification queues."""

import csv
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_http_verification_queue.py"


class VerificationQueueTest(unittest.TestCase):
    def test_selects_only_unmeasured_domains_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            catalog = work / "catalog.csv.gz"
            with gzip.open(catalog, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("domain", "http_classification"))
                writer.writerow(("a.ro", "not_measured_http"))
                writer.writerow(("b.ro", "available_at_measurement"))
                writer.writerow(("c.ro", "not_measured_http"))
            output = work / "queue"
            subprocess.run(
                [
                    sys.executable, str(SCRIPT), "--catalog", str(catalog),
                    "--output-dir", str(output), "--shards", "2",
                    "--stratum", "test_verification",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            rows = []
            for path in sorted(output.glob("domains-*.csv")):
                with path.open(encoding="utf-8", newline="") as handle:
                    rows.extend(csv.DictReader(handle))
            self.assertEqual({row["domain"] for row in rows}, {"a.ro", "c.ro"})
            self.assertTrue(all(row["stratum"] == "test_verification" for row in rows))


if __name__ == "__main__":
    unittest.main()
