import tempfile
import unittest
from pathlib import Path

from http_pilot import (
    classify_candidate,
    classify_page,
    extract_page,
    is_local_network_error,
    normalized_url,
    open_database,
    parse_sitemap,
    write_result,
)
from company_identity import sanitize_html_bytes


class HttpPilotTests(unittest.TestCase):
    def test_sanitize_legacy_numeric_entity(self):
        self.assertEqual(
            sanitize_html_bytes(b"Romania &#259casa &#537;i"),
            b"Romania &#259;casa &#537;i",
        )

    def test_local_network_error_detection(self):
        self.assertTrue(
            is_local_network_error(
                "ClientConnectorError: [The network location cannot be reached.]"
            )
        )
        self.assertTrue(is_local_network_error("OSError:[WinError 10051] unreachable network"))
        self.assertFalse(
            is_local_network_error(
                "ClientConnectorDNSError: getaddrinfo failed"
            )
        )
        self.assertFalse(is_local_network_error("TimeoutError:"))

    def test_normalized_url(self):
        self.assertEqual(
            normalized_url("../contact#team", "https://WWW.Example.ro/a/b"),
            "https://www.example.ro/contact",
        )
        self.assertIsNone(normalized_url("ftp://example.ro/file"))
        self.assertIsNone(normalized_url("https://user:pass@example.ro/"))

    def test_classification(self):
        classes, score = classify_candidate(
            "https://example.ro/politica-de-confidentialitate", "Confidențialitate", "footer"
        )
        self.assertIn("privacy", classes)
        self.assertGreaterEqual(score, 90)
        self.assertIn(
            "terms",
            classify_page("https://example.ro/terms", "Termeni și condiții", "Termeni și condiții generale"),
        )
        self.assertEqual(
            classify_page("https://example.ro/produs", "Produs", "Contact Termeni Privacy Contact Termeni Privacy"),
            [],
        )

    def test_sitemap_index_and_urlset(self):
        kind, urls, children, error = parse_sitemap(
            b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.ro/pages.xml</loc></sitemap></sitemapindex>',
            "https://example.ro/sitemap.xml",
        )
        self.assertEqual((kind, urls, error), ("index", [], None))
        self.assertEqual(children, ["https://example.ro/pages.xml"])

        kind, urls, children, error = parse_sitemap(
            b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.ro/contact</loc><lastmod>2026-01-01</lastmod></url></urlset>',
            "https://example.ro/sitemap.xml",
        )
        self.assertEqual(kind, "urlset")
        self.assertEqual(urls, [("https://example.ro/contact", "2026-01-01")])
        self.assertEqual(children, [])
        self.assertIsNone(error)

    def test_html_extraction(self):
        page = extract_page(
            b'<html lang="ro"><head><title>Contact</title></head><body><footer><a href="/privacy-policy">Privacy</a> Email office@example.ro CUI RO12345678</footer></body></html>',
            "https://example.ro/",
            "example.ro",
        )
        self.assertEqual(page["title"], "Contact")
        self.assertIn("office@example.ro", page["emails"])
        self.assertIn("RO12345678", page["cuis"])
        self.assertEqual(page["links"][0]["location"], "footer")
        self.assertEqual(page["company_identity"]["cuis"][0]["cui"], "RO12345678")

    def test_database_write_is_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = open_database(Path(directory) / "pilot.sqlite3")
            result = {
                "domain": "example.ro", "stratum": "test", "status": "complete",
                "origin_url": "https://example.ro/", "final_url": "https://example.ro/",
                "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:01+00:00",
                "error": None, "fetches": [], "pages": [], "sitemaps": [], "urls": [],
            }
            write_result(connection, result)
            write_result(connection, result)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":
    unittest.main()
