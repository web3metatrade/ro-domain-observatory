import tempfile
import unittest
from pathlib import Path

from batched_http_supervisor import database_stats
from http_pilot import open_database, write_result


class BatchedHttpSupervisorTest(unittest.TestCase):
    def test_stats_are_limited_to_selected_domains(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "all.sqlite3"
            connection = open_database(database)
            for domain, status in (
                ("selected-complete.ro", "complete"),
                ("selected-retry.ro", "no_origin"),
                ("outside.ro", "complete"),
            ):
                write_result(connection, {
                    "domain": domain, "stratum": "test", "status": status,
                    "origin_url": None, "final_url": None, "started_at": "start",
                    "finished_at": "finish", "error": None, "fetches": [],
                    "pages": [], "sitemaps": [], "urls": [],
                })
            connection.close()
            stored, unseen, statuses = database_stats(
                database,
                ["selected-complete.ro", "selected-retry.ro", "unseen.ro"],
                ("no_origin",),
            )
            self.assertEqual((stored, unseen), (2, 1))
            self.assertEqual(statuses, {"complete": 1, "no_origin": 1})


if __name__ == "__main__":
    unittest.main()
