import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from consolidate_http import consolidate


SOURCE_SCHEMA = """
CREATE TABLE crawl_runs (
    run_id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    configuration_json TEXT NOT NULL
);
CREATE TABLE sites (
    domain TEXT PRIMARY KEY,
    stratum TEXT,
    status TEXT NOT NULL,
    origin_url TEXT,
    final_url TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    error TEXT,
    fetch_count INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    sitemap_count INTEGER NOT NULL,
    sitemap_url_count INTEGER NOT NULL,
    discovered_url_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE fetches (
    fetch_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    purpose TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    content_type TEXT,
    bytes INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    sha256 TEXT,
    error TEXT,
    redirects_json TEXT NOT NULL,
    truncated INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE sitemaps (
    sitemap_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    kind TEXT NOT NULL,
    url_count INTEGER NOT NULL,
    child_count INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE discovered_urls (
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    classes_json TEXT NOT NULL,
    score INTEGER NOT NULL,
    lastmod TEXT,
    PRIMARY KEY(domain, url, source)
) WITHOUT ROWID;
CREATE TABLE pages (
    page_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    title TEXT,
    language TEXT,
    classes_json TEXT NOT NULL,
    emails_json TEXT NOT NULL,
    phones_json TEXT NOT NULL,
    cuis_json TEXT NOT NULL,
    jsonld_json TEXT NOT NULL,
    company_identity_json TEXT NOT NULL DEFAULT '{}',
    text_sha256 TEXT,
    excerpt TEXT,
    source TEXT NOT NULL,
    score INTEGER NOT NULL,
    soft_404 INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
"""


def make_source(path: Path, domain: str, status: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SOURCE_SCHEMA)
    now = "2026-08-21T00:00:00+00:00"
    connection.execute(
        "INSERT INTO crawl_runs VALUES(1,?,?,?)", (now, now, json.dumps({"test": True}))
    )
    connection.execute(
        """
        INSERT INTO sites VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            domain,
            "test",
            status,
            None,
            None,
            now,
            now,
            None,
            0,
            0,
            0,
            0,
            0,
        ),
    )
    connection.commit()
    connection.close()


class ConsolidateHttpTests(unittest.TestCase):
    def test_consolidates_disjoint_sources_and_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            first = directory / "first.sqlite3"
            second = directory / "second.sqlite3"
            output = directory / "combined.sqlite3"
            make_source(first, "a.ro", "complete")
            make_source(second, "b.ro", "no_origin")

            summary = consolidate(
                output,
                [("first", first), ("second", second)],
                expected_domains=2,
            )

            self.assertEqual(summary["counts"]["sites"], 2)
            self.assertEqual(summary["source_counts"], {"first": 1, "second": 1})
            self.assertEqual(summary["quick_check"], "ok")
            self.assertTrue(output.with_suffix(".summary.json").exists())
            connection = sqlite3.connect(output)
            self.assertEqual(
                connection.execute(
                    "SELECT domain,status FROM sites ORDER BY domain"
                ).fetchall(),
                [("a.ro", "complete"), ("b.ro", "no_origin")],
            )
            connection.close()

    def test_rejects_overlapping_domains(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            first = directory / "first.sqlite3"
            second = directory / "second.sqlite3"
            make_source(first, "duplicate.ro", "complete")
            make_source(second, "duplicate.ro", "no_origin")

            with self.assertRaises(sqlite3.IntegrityError):
                consolidate(
                    directory / "combined.sqlite3",
                    [("first", first), ("second", second)],
                    expected_domains=1,
                )

    def test_accepts_a_previously_consolidated_database_as_a_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            first = directory / "first.sqlite3"
            second = directory / "second.sqlite3"
            third = directory / "third.sqlite3"
            base = directory / "base.sqlite3"
            output = directory / "combined.sqlite3"
            make_source(first, "a.ro", "complete")
            make_source(second, "b.ro", "no_origin")
            make_source(third, "c.ro", "robots_blocked")
            consolidate(base, [("first", first), ("second", second)], expected_domains=2)

            summary = consolidate(
                output,
                [("base", base), ("third", third)],
                expected_domains=3,
            )

            self.assertEqual(summary["counts"]["sites"], 3)
            self.assertEqual(
                summary["source_counts"],
                {"base:first": 1, "base:second": 1, "third": 1},
            )
            connection = sqlite3.connect(output)
            self.assertEqual(
                connection.execute(
                    "SELECT source_name,source_run_id FROM source_runs ORDER BY source_name"
                ).fetchall(),
                [("base:first", 1), ("base:second", 1), ("third", 1)],
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
