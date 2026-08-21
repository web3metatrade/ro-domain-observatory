import tempfile
import unittest
from pathlib import Path

from http_pilot import open_database, write_result
from summarize_http_database import summarize


class SummarizeHttpDatabaseTest(unittest.TestCase):
    def test_validates_existing_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "http.sqlite3"
            connection = open_database(database)
            write_result(connection, {
                "domain": "example.ro", "stratum": "test", "status": "complete",
                "origin_url": "https://example.ro/", "final_url": "https://example.ro/",
                "started_at": "start", "finished_at": "finish", "error": None,
                "fetches": [], "pages": [], "sitemaps": [], "urls": [],
            })
            connection.close()
            report = summarize(database, 1, Path(temporary) / "summary.json")
            self.assertEqual(report["quick_check"], "ok")
            self.assertEqual(report["statuses"], {"complete": 1})
            self.assertEqual(report["counts"]["sites"], 1)


if __name__ == "__main__":
    unittest.main()
