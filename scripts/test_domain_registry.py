import tempfile
import unittest
from pathlib import Path

from domain_registry import close_connection, connect, initialize, normalize_hostname


class NormalizationTests(unittest.TestCase):
    def test_apex_and_www_collapse_to_same_domain(self):
        self.assertEqual(normalize_hostname("https://www.Example.ro/contact")[1], "example.ro")

    def test_ro_second_level_suffix(self):
        self.assertEqual(normalize_hostname("shop.example.com.ro")[1], "example.com.ro")

    def test_idn(self):
        normalized = normalize_hostname("https://școală.ro/")
        self.assertEqual(normalized[1], "xn--coal-3sa77n.ro")

    def test_rejects_non_ro_and_ip(self):
        self.assertIsNone(normalize_hostname("example.com"))
        self.assertIsNone(normalize_hostname("127.0.0.1"))


class SchemaTests(unittest.TestCase):
    def test_schema_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            con = connect(Path(directory) / "test.sqlite3")
            try:
                initialize(con)
                self.assertGreater(con.execute("SELECT count(*) FROM sources").fetchone()[0], 20)
            finally:
                close_connection(con)


if __name__ == "__main__":
    unittest.main()
