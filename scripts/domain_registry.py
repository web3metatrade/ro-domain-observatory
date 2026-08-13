#!/usr/bin/env python3
"""Build a provenance-aware, deduplicated registry of observed .ro domains."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import ipaddress
import json
import re
import sqlite3
import sys
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ro_domains.sqlite3"
SCHEMA_PATH = ROOT / "schema.sql"
SOURCES_PATH = ROOT / "sources.json"

RO_PUBLIC_SUFFIXES = {
    "arts.ro", "com.ro", "firm.ro", "info.ro", "nom.ro", "nt.ro",
    "org.ro", "rec.ro", "store.ro", "tm.ro", "www.ro",
}

ASCII_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_hostname(raw: str) -> tuple[str, str, str | None, str | None] | None:
    """Return (ascii hostname, registrable domain, unicode host, unicode domain)."""
    value = (raw or "").strip().strip("\"'").lower()
    if not value:
        return None
    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[-1]
    if "://" not in value:
        value = "//" + value
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lstrip("*.")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(ascii_host) > 253:
        return None
    labels = ascii_host.split(".")
    if len(labels) < 2 or labels[-1] != "ro":
        return None
    if any(not ASCII_LABEL.fullmatch(label) for label in labels):
        return None
    suffix = ".".join(labels[-2:])
    if suffix in RO_PUBLIC_SUFFIXES:
        if len(labels) < 3:
            return None
        domain = ".".join(labels[-3:])
    else:
        domain = suffix
    try:
        unicode_host = ascii_host.encode("ascii").decode("idna")
        unicode_domain = domain.encode("ascii").decode("idna")
    except UnicodeError:
        unicode_host = None
        unicode_domain = None
    return ascii_host, domain, unicode_host, unicode_domain


def reverse_commoncrawl_domain(value: str) -> str:
    parts = value.strip().strip(".").split(".")
    return ".".join(reversed(parts))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def completed_import_exists(con: sqlite3.Connection, source_id: str, digest: str) -> bool:
    return con.execute(
        "SELECT 1 FROM import_runs WHERE source_id=? AND input_sha256=? AND status='completed' LIMIT 1",
        (source_id, digest),
    ).fetchone() is not None


def load_sources() -> list[dict]:
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA cache_size = -131072")
    return con


def close_connection(con: sqlite3.Connection) -> None:
    """Checkpoint WAL files before closing (important for removable/temp paths)."""
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def sync_sources(con: sqlite3.Connection) -> None:
    fields = [
        "source_id", "name", "category", "url", "access_method", "coverage",
        "license", "redistribution_status", "public_export", "import_status",
        "notes", "last_checked_at",
    ]
    sql = f"""
        INSERT INTO sources ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})
        ON CONFLICT(source_id) DO UPDATE SET
            name=excluded.name, category=excluded.category, url=excluded.url,
            access_method=excluded.access_method, coverage=excluded.coverage,
            license=excluded.license,
            redistribution_status=excluded.redistribution_status,
            public_export=excluded.public_export,
            import_status=excluded.import_status, notes=excluded.notes,
            last_checked_at=excluded.last_checked_at
    """
    con.executemany(sql, [[row.get(field) for field in fields] for row in load_sources()])


def initialize(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    sync_sources(con)
    con.commit()


@contextmanager
def text_reader(path: Path):
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
            yield handle
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError("ZIP imports must contain exactly one data file")
            with archive.open(names[0]) as raw:
                with io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="") as handle:
                    yield handle
    else:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            yield handle


class RegistryImporter:
    def __init__(self, con: sqlite3.Connection, source_id: str, observed_at: str, input_uri: str, digest: str | None):
        self.con = con
        self.source_id = source_id
        self.observed_at = observed_at
        self.rows = self.accepted = self.domains_new = self.hostnames_new = self.rejected = 0
        cur = con.execute(
            "INSERT INTO import_runs(source_id,input_uri,input_sha256,started_at,status) VALUES(?,?,?,?,?)",
            (source_id, input_uri, digest, utc_now(), "running"),
        )
        self.run_id = cur.lastrowid

    def add(self, raw: str, rank: int | None = None, metadata: dict | None = None) -> None:
        self.rows += 1
        normalized = normalize_hostname(raw)
        if normalized is None:
            self.rejected += 1
            return
        hostname, domain, unicode_host, unicode_domain = normalized
        before = self.con.total_changes
        self.con.execute(
            "INSERT OR IGNORE INTO domains(domain,unicode_domain,first_seen_at,last_seen_at) VALUES(?,?,?,?)",
            (domain, unicode_domain, self.observed_at, self.observed_at),
        )
        if self.con.total_changes > before:
            self.domains_new += 1
        self.con.execute("UPDATE domains SET last_seen_at=max(last_seen_at,?) WHERE domain=?", (self.observed_at, domain))
        before = self.con.total_changes
        self.con.execute(
            "INSERT OR IGNORE INTO hostnames(hostname,registrable_domain,unicode_hostname,first_seen_at,last_seen_at) VALUES(?,?,?,?,?)",
            (hostname, domain, unicode_host, self.observed_at, self.observed_at),
        )
        if self.con.total_changes > before:
            self.hostnames_new += 1
        self.con.execute("UPDATE hostnames SET last_seen_at=max(last_seen_at,?) WHERE hostname=?", (self.observed_at, hostname))
        meta_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None
        self.con.execute(
            """INSERT INTO domain_sources(domain,source_id,first_seen_at,last_seen_at,occurrences,best_rank,metadata_json)
               VALUES(?,?,?,?,1,?,?)
               ON CONFLICT(domain,source_id) DO UPDATE SET
                 last_seen_at=max(last_seen_at,excluded.last_seen_at),
                 occurrences=domain_sources.occurrences+1,
                 best_rank=CASE
                   WHEN excluded.best_rank IS NULL THEN domain_sources.best_rank
                   WHEN domain_sources.best_rank IS NULL THEN excluded.best_rank
                   ELSE min(domain_sources.best_rank,excluded.best_rank) END,
                 metadata_json=coalesce(domain_sources.metadata_json,excluded.metadata_json)""",
            (domain, self.source_id, self.observed_at, self.observed_at, rank, meta_json),
        )
        self.con.execute(
            """INSERT INTO hostname_sources(hostname,source_id,first_seen_at,last_seen_at,occurrences,best_rank)
               VALUES(?,?,?,?,1,?)
               ON CONFLICT(hostname,source_id) DO UPDATE SET
                 last_seen_at=max(last_seen_at,excluded.last_seen_at),
                 occurrences=hostname_sources.occurrences+1,
                 best_rank=CASE
                   WHEN excluded.best_rank IS NULL THEN hostname_sources.best_rank
                   WHEN hostname_sources.best_rank IS NULL THEN excluded.best_rank
                   ELSE min(hostname_sources.best_rank,excluded.best_rank) END""",
            (hostname, self.source_id, self.observed_at, self.observed_at, rank),
        )
        self.accepted += 1
        if self.rows % 100_000 == 0:
            self.con.commit()
            print(f"{self.source_id}: {self.rows:,} rows, {self.domains_new:,} new domains", file=sys.stderr)

    def finish(self, status: str = "completed", error: str | None = None) -> None:
        self.con.execute(
            """UPDATE import_runs SET finished_at=?,rows_read=?,hostnames_accepted=?,domains_new=?,
               hostnames_new=?,rejected=?,status=?,error=? WHERE run_id=?""",
            (utc_now(), self.rows, self.accepted, self.domains_new, self.hostnames_new,
             self.rejected, status, error, self.run_id),
        )
        self.con.commit()


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def import_file(args, con: sqlite3.Connection) -> None:
    path = Path(args.path).resolve()
    observed_at = args.observed_at or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    digest = sha256_file(path)
    if completed_import_exists(con, args.source, digest):
        print(json.dumps({"source": args.source, "status": "already_imported", "sha256": digest}, indent=2))
        return
    importer = RegistryImporter(con, args.source, observed_at, str(path), digest)
    try:
        with text_reader(path) as handle:
            if args.format == "plain":
                for line in handle:
                    value = line.strip().split()[0] if line.strip() else ""
                    # Bulk domain lists are overwhelmingly non-.ro. Reject them
                    # before URL/IDNA parsing so multi-hundred-million row files
                    # remain practical to stream on one machine.
                    importer.rows += 1
                    if not value.lower().rstrip(".").endswith(".ro"):
                        continue
                    importer.rows -= 1
                    importer.add(value)
            elif args.format == "commoncrawl-domain-vertices":
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 2:
                        importer.rows += 1
                        importer.rejected += 1
                        continue
                    reversed_domain = parts[1]
                    if reversed_domain == "ro" or reversed_domain.startswith("ro."):
                        importer.add(reverse_commoncrawl_domain(reversed_domain), metadata={"host_count": parse_int(parts[2]) if len(parts) > 2 else None})
                    else:
                        importer.rows += 1
            elif args.format == "csv":
                reader = csv.DictReader(handle)
                if not reader.fieldnames or args.column not in reader.fieldnames:
                    raise ValueError(f"CSV column {args.column!r} not found; available: {reader.fieldnames}")
                for row in reader:
                    importer.add(row.get(args.column, ""), parse_int(row.get(args.rank_column)) if args.rank_column else None)
            elif args.format == "rank-domain-csv":
                for row in csv.reader(handle):
                    if len(row) < 2:
                        importer.rows += 1
                        importer.rejected += 1
                        continue
                    importer.add(row[1], parse_int(row[0]))
            else:
                raise ValueError(f"Unsupported format: {args.format}")
        importer.finish()
    except Exception as exc:
        importer.finish("failed", str(exc))
        raise
    print(json.dumps({
        "source": args.source, "rows_read": importer.rows,
        "hostnames_accepted": importer.accepted, "domains_new": importer.domains_new,
        "hostnames_new": importer.hostnames_new, "rejected": importer.rejected,
    }, ensure_ascii=False, indent=2))


def import_cui_repo(args, con: sqlite3.Connection) -> None:
    repo = Path(args.repo).resolve()
    files = sorted((repo / "data" / "contacts").glob("part-*.csv"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.read_bytes())
    source_digest = digest.hexdigest()
    if completed_import_exists(con, "cui_ro_dataset", source_digest):
        print(json.dumps({"source": "cui_ro_dataset", "status": "already_imported", "sha256": source_digest}, indent=2))
        return
    importer = RegistryImporter(con, "cui_ro_dataset", args.observed_at or datetime.now().date().isoformat(), str(repo), source_digest)
    try:
        for path in files:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("type") == "website":
                        importer.add(row.get("value", ""), metadata={"cui": row.get("cui"), "status": row.get("status")})
        importer.finish()
    except Exception as exc:
        importer.finish("failed", str(exc))
        raise
    print(json.dumps({"source": "cui_ro_dataset", "domains_new": importer.domains_new, "accepted": importer.accepted}, indent=2))


def import_osm_pbf(args, con: sqlite3.Connection) -> None:
    """Import .ro domains from website and e-mail tags in an OSM PBF extract."""
    try:
        import osmium
    except ImportError as exc:
        raise SystemExit("OSM PBF imports require: python -m pip install osmium") from exc

    path = Path(args.path).resolve()
    observed_at = args.observed_at or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    digest = sha256_file(path)
    source_id = "openstreetmap"
    if completed_import_exists(con, source_id, digest):
        print(json.dumps({"source": source_id, "status": "already_imported", "sha256": digest}, indent=2))
        return
    importer = RegistryImporter(con, source_id, observed_at, str(path), digest)
    website_keys = ("website", "contact:website", "url", "contact:url")
    email_keys = ("email", "contact:email")

    class WebsiteHandler(osmium.SimpleHandler):
        def collect(self, obj, object_type: str) -> None:
            for key in website_keys + email_keys:
                value = obj.tags.get(key)
                if not value:
                    continue
                for item in value.split(";"):
                    importer.add(item.strip(), metadata={
                        "osm_type": object_type,
                        "osm_id": str(obj.id),
                        "tag": key,
                    })

        def node(self, obj):
            self.collect(obj, "node")

        def way(self, obj):
            self.collect(obj, "way")

        def relation(self, obj):
            self.collect(obj, "relation")

    try:
        WebsiteHandler().apply_file(str(path), locations=False)
        importer.finish()
    except Exception as exc:
        importer.finish("failed", str(exc))
        raise
    print(json.dumps({
        "source": source_id, "rows_read": importer.rows,
        "hostnames_accepted": importer.accepted, "domains_new": importer.domains_new,
        "hostnames_new": importer.hostnames_new, "rejected": importer.rejected,
    }, ensure_ascii=False, indent=2))


def export_domains(args, con: sqlite3.Connection) -> None:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    public_filter = "AND s.public_export=1" if args.public_only else ""
    query = f"""
        SELECT d.domain,d.unicode_domain,d.first_seen_at,d.last_seen_at,
               count(DISTINCT ds.source_id) AS source_count,
               group_concat(DISTINCT ds.source_id) AS sources
        FROM domains d
        JOIN domain_sources ds ON ds.domain=d.domain
        JOIN sources s ON s.source_id=ds.source_id
        WHERE 1=1 {public_filter}
        GROUP BY d.domain,d.unicode_domain,d.first_seen_at,d.last_seen_at
        ORDER BY d.domain
    """
    opener = gzip.open if output.suffix == ".gz" else open
    with opener(output, "wt", encoding="utf-8", newline="") as handle:
        if args.plain:
            plain_query = f"""
                SELECT d.domain FROM domains d
                JOIN domain_sources ds ON ds.domain=d.domain
                JOIN sources s ON s.source_id=ds.source_id
                WHERE 1=1 {public_filter}
                GROUP BY d.domain ORDER BY d.domain
            """
            for (domain,) in con.execute(plain_query):
                handle.write(domain + "\n")
        else:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["domain", "unicode_domain", "first_seen_at", "last_seen_at", "source_count", "sources"])
            writer.writerows(con.execute(query))
    print(json.dumps({"output": str(output), "sha256": sha256_file(output), "bytes": output.stat().st_size}, indent=2))


def show_stats(con: sqlite3.Connection) -> None:
    totals = {
        "domains": con.execute("SELECT count(*) FROM domains").fetchone()[0],
        "public_export_domains": con.execute(
            """SELECT count(DISTINCT ds.domain) FROM domain_sources ds
               JOIN sources s USING(source_id) WHERE s.public_export=1"""
        ).fetchone()[0],
        "hostnames": con.execute("SELECT count(*) FROM hostnames").fetchone()[0],
        "sources_catalogued": con.execute("SELECT count(*) FROM sources").fetchone()[0],
        "sources_imported": con.execute("SELECT count(DISTINCT source_id) FROM import_runs WHERE status='completed'").fetchone()[0],
    }
    totals["by_source"] = [dict(zip(("source_id", "domains", "public_export"), row)) for row in con.execute(
        """SELECT ds.source_id,count(*),s.public_export FROM domain_sources ds
           JOIN sources s USING(source_id) GROUP BY ds.source_id,s.public_export ORDER BY count(*) DESC"""
    )]
    print(json.dumps(totals, ensure_ascii=False, indent=2))


def verify_database(con: sqlite3.Connection) -> None:
    checks = {
        "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_errors": len(con.execute("PRAGMA foreign_key_check").fetchall()),
        "invalid_ro_suffix": con.execute(
            "SELECT count(*) FROM domains WHERE substr(domain, -3) <> '.ro'"
        ).fetchone()[0],
        "invalid_seen_order": con.execute(
            "SELECT count(*) FROM domains WHERE first_seen_at > last_seen_at"
        ).fetchone()[0],
        "orphan_hostnames": con.execute(
            """SELECT count(*) FROM hostnames h LEFT JOIN domains d
               ON d.domain=h.registrable_domain WHERE d.domain IS NULL"""
        ).fetchone()[0],
        "unfinished_imports": con.execute(
            "SELECT count(*) FROM import_runs WHERE status='running'"
        ).fetchone()[0],
    }
    checks["ok"] = checks == {
        "integrity_check": "ok",
        "foreign_key_errors": 0,
        "invalid_ro_suffix": 0,
        "invalid_seen_order": 0,
        "orphan_hostnames": 0,
        "unfinished_imports": 0,
    }
    print(json.dumps(checks, indent=2))
    if not checks["ok"]:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    imp = sub.add_parser("import-file")
    imp.add_argument("--source", required=True)
    imp.add_argument("--path", required=True)
    imp.add_argument("--format", choices=("plain", "csv", "rank-domain-csv", "commoncrawl-domain-vertices"), required=True)
    imp.add_argument("--column")
    imp.add_argument("--rank-column")
    imp.add_argument("--observed-at")
    cui = sub.add_parser("import-cui-repo")
    cui.add_argument("--repo", required=True)
    cui.add_argument("--observed-at")
    osm = sub.add_parser("import-osm-pbf")
    osm.add_argument("--path", required=True)
    osm.add_argument("--observed-at")
    exp = sub.add_parser("export")
    exp.add_argument("--output", required=True)
    exp.add_argument("--public-only", action="store_true")
    exp.add_argument("--plain", action="store_true", help="write one normalized domain per line")
    sub.add_parser("stats")
    sub.add_parser("verify")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db_path = Path(args.db).resolve()
    with connect(db_path) as con:
        initialize(con)
        if args.command == "init":
            print(json.dumps({"database": str(db_path), "sources": len(load_sources())}, indent=2))
        elif args.command == "import-file":
            if args.format == "csv" and not args.column:
                raise SystemExit("--column is required for CSV imports")
            import_file(args, con)
        elif args.command == "import-cui-repo":
            import_cui_repo(args, con)
        elif args.command == "import-osm-pbf":
            import_osm_pbf(args, con)
        elif args.command == "export":
            export_domains(args, con)
        elif args.command == "stats":
            show_stats(con)
        elif args.command == "verify":
            verify_database(con)


if __name__ == "__main__":
    main()
