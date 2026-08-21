import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dns_nxdomain_consensus import (
    aggregate_summaries,
    apply_consensus,
    apply_unresolved,
    build_input,
    build_remaining_input,
)
from http_pilot import open_database, write_result


def queue(path: Path, domains: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("domain", "stratum"))
        for domain in domains:
            writer.writerow((domain, "test"))


def results(path: Path, statuses: dict[str, str], resolver: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for domain in sorted(statuses):
            handle.write(
                json.dumps(
                    {
                        "name": domain,
                        "status": statuses[domain],
                        "timestamp": "2026-08-21T01:02:03+00:00",
                        "data": {"resolver": resolver},
                    }
                )
                + "\n"
            )


class DnsNxdomainConsensusTests(unittest.TestCase):
    def test_build_and_apply_preserves_http_and_requires_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            queues = [root / "q0.csv", root / "q1.csv"]
            databases = [root / "d0.sqlite3", root / "d1.sqlite3"]
            queue(queues[0], ["a.ro", "b.ro"])
            queue(queues[1], ["c.ro", "d.ro"])
            for database in databases:
                open_database(database).close()
            connection = open_database(databases[0])
            write_result(
                connection,
                {
                    "domain": "a.ro",
                    "stratum": "http",
                    "status": "complete",
                    "origin_url": "https://a.ro/",
                    "final_url": "https://a.ro/",
                    "started_at": "2026-08-21T00:00:00+00:00",
                    "finished_at": "2026-08-21T00:00:01+00:00",
                    "error": None,
                    "fetches": [],
                    "pages": [],
                    "sitemaps": [],
                    "urls": [],
                },
            )
            connection.close()

            first = root / "first.jsonl"
            second = root / "second.jsonl"
            results(first, {"a.ro": "NXDOMAIN", "b.ro": "NXDOMAIN", "c.ro": "NXDOMAIN", "d.ro": "SERVFAIL"}, "1.1.1.1:53")
            results(second, {"a.ro": "NXDOMAIN", "b.ro": "NXDOMAIN", "c.ro": "NOERROR", "d.ro": "TIMEOUT"}, "8.8.8.8:53")
            input_path = root / "domains.txt"
            input_report = build_input(queues, input_path)
            self.assertEqual(input_report["domains"], 4)
            self.assertEqual(input_path.read_text(encoding="ascii"), "a.ro\nb.ro\nc.ro\nd.ro\n")

            report = apply_consensus(
                queues, databases, [first, second], 4, root / "summary.json"
            )
            self.assertEqual(report["consensus_nxdomain"], 2)
            connection = sqlite3.connect(databases[0])
            rows = dict(connection.execute("SELECT domain,status FROM sites"))
            errors = dict(connection.execute("SELECT domain,error FROM sites"))
            connection.close()
            self.assertEqual(rows, {"a.ro": "complete", "b.ro": "dns_nxdomain"})
            self.assertIsNone(errors["a.ro"])
            self.assertEqual(errors["b.ro"], "dns_nxdomain_consensus")
            connection = sqlite3.connect(databases[1])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0], 0)
            connection.close()
            unresolved = apply_unresolved(
                queues, databases, [first, second], 4, root / "unresolved.json"
            )
            self.assertEqual(unresolved["dns_unresolved"], 1)
            self.assertEqual(unresolved["resolvable_for_http"], 1)
            connection = sqlite3.connect(databases[1])
            self.assertEqual(
                connection.execute("SELECT domain,status FROM sites").fetchall(),
                [("d.ro", "dns_unresolved")],
            )
            connection.close()
            remaining = build_remaining_input(
                queues, databases, root / "remaining", root / "remaining.txt"
            )
            self.assertEqual(remaining["domains"], 2)
            self.assertEqual((root / "remaining.txt").read_text(), "c.ro\nd.ro\n")
            aggregate = aggregate_summaries(
                [root / "summary.json"], 4, root / "aggregate.json",
                root / "unresolved.json",
            )
            self.assertEqual(aggregate["consensus_nxdomain"], 2)
            self.assertEqual(aggregate["dns_unresolved"], 1)
            self.assertEqual(aggregate["dns_final_dispositions"], 3)
            self.assertEqual(aggregate["phases"][0]["queue_domains"], 4)

    def test_rejects_incomplete_result_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            queue_path = root / "queue.csv"
            database = root / "db.sqlite3"
            queue(queue_path, ["a.ro", "b.ro"])
            open_database(database).close()
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            results(first, {"a.ro": "NXDOMAIN", "b.ro": "NXDOMAIN"}, "one")
            results(second, {"a.ro": "NXDOMAIN"}, "two")
            with self.assertRaisesRegex(ValueError, "scope mismatch"):
                apply_consensus(
                    [queue_path], [database], [first, second], 2, root / "summary.json"
                )


if __name__ == "__main__":
    unittest.main()
