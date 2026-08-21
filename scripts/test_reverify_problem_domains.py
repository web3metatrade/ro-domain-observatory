import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from http_pilot import open_database, write_result
from reverify_problem_domains import analyze, apply, build_queue, merge


def result(path: Path, statuses: dict[str, str], resolver: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for domain in sorted(statuses):
            handle.write(json.dumps({
                "name": domain,
                "status": statuses[domain],
                "timestamp": "2026-08-22T00:00:00+00:00",
                "data": {"resolver": resolver},
            }) + "\n")


class ReverifyProblemDomainsTest(unittest.TestCase):
    def test_two_phase_dispositions_and_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "derived.sqlite3"
            con = open_database(database)
            for domain, status in (("a.ro", "no_origin"), ("b.ro", "dns_unresolved"), ("c.ro", "no_origin"), ("d.ro", "complete")):
                write_result(con, {
                    "domain": domain, "stratum": "old", "status": status,
                    "origin_url": None, "final_url": None, "started_at": "old",
                    "finished_at": "old", "error": "old", "fetches": [],
                    "pages": [], "sitemaps": [], "urls": [],
                })
            con.close()
            queue = root / "queue.csv"
            built = build_queue(database, queue, root / "input.txt", root / "build.json")
            self.assertEqual(built["domains"], 3)
            p1_results = []
            values = (
                {"a.ro": "NOERROR", "b.ro": "NXDOMAIN", "c.ro": "SERVFAIL"},
                {"a.ro": "NXDOMAIN", "b.ro": "NXDOMAIN", "c.ro": "TIMEOUT"},
                {"a.ro": "SERVFAIL", "b.ro": "SERVFAIL", "c.ro": "NXDOMAIN"},
            )
            for index, statuses in enumerate(values):
                path = root / f"p1-{index}.jsonl"
                result(path, statuses, str(index))
                p1_results.append(path)
            phase1 = root / "phase1"
            first = analyze(queue, p1_results, phase1, 3, 2)
            self.assertEqual(first["partitions"]["resolvable"]["rows"], 1)
            self.assertEqual(first["partitions"]["nxdomain"]["rows"], 1)
            self.assertEqual(first["partitions"]["unresolved"]["rows"], 1)
            p2_results = []
            for index, status in enumerate(("NOERROR", "TIMEOUT", "SERVFAIL")):
                path = root / f"p2-{index}.jsonl"
                result(path, {"c.ro": status}, str(index))
                p2_results.append(path)
            phase2 = root / "phase2"
            analyze(
                phase1 / "unresolved.csv", p2_results, phase2, 1, 2,
                authoritative_result=p2_results[0],
            )
            final = root / "final"
            report = merge(phase1, phase2, final, 3)
            self.assertEqual(report["partitions"]["resolvable"]["rows"], 2)
            applied = apply(database, final, 3, root / "apply.json")
            self.assertEqual(applied["queued_for_http"], 2)
            con = sqlite3.connect(database)
            rows = dict(con.execute("SELECT domain,status FROM sites"))
            con.close()
            self.assertEqual(rows, {"b.ro": "dns_nxdomain", "d.ro": "complete"})


if __name__ == "__main__":
    unittest.main()
