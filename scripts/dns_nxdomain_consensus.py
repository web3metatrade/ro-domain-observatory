#!/usr/bin/env python3
"""Build a DNS input and apply multi-resolver NXDOMAIN consensus to HTTP shards."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from export_public_http import normalize_domain


RETRYABLE = ("local_network_error", "worker_error")
DNS_STRATUM = "dns_nxdomain_consensus_2026_08_21"
DNS_ERROR = "dns_nxdomain_consensus"
DNS_UNRESOLVED_STRATUM = "dns_unresolved_after_retries_2026_08_21"
DNS_UNRESOLVED_ERROR = "dns_unresolved_after_retries"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_queues(paths: list[Path]) -> tuple[list[dict[str, str]], dict[str, int]]:
    rows_by_shard: list[dict[str, str]] = []
    domain_shard: dict[str, int] = {}
    for shard, raw_path in enumerate(paths):
        path = raw_path.resolve()
        rows: dict[str, str] = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "domain" not in reader.fieldnames:
                raise ValueError(f"queue has no domain column: {path}")
            for line, row in enumerate(reader, 2):
                domain = normalize_domain(row["domain"])
                if not domain or not domain.endswith(".ro"):
                    raise ValueError(f"invalid queue domain at {path}:{line}")
                if domain in domain_shard:
                    raise ValueError(f"duplicate queue domain: {domain}")
                rows[domain] = row.get("stratum") or "verification"
                domain_shard[domain] = shard
        rows_by_shard.append(rows)
    return rows_by_shard, domain_shard


def build_input(queues: list[Path], output: Path) -> dict[str, object]:
    _, domain_shard = load_queues(queues)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="ascii", newline="") as handle:
        for domain in sorted(domain_shard):
            handle.write(f"{domain}\n")
    return {
        "output": str(output),
        "domains": len(domain_shard),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "queues": [str(path.resolve()) for path in queues],
    }


def build_remaining_input(
    queues: list[Path], databases: list[Path], output_dir: Path, output: Path
) -> dict[str, object]:
    if len(queues) != len(databases):
        raise ValueError("queue and database counts must match")
    rows_by_shard, _ = load_queues(queues)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    remaining_paths: list[Path] = []
    remaining_domains: set[str] = set()
    shard_reports: list[dict[str, object]] = []
    for shard, database in enumerate(databases):
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=120
        )
        completed = {row[0] for row in connection.execute("SELECT domain FROM sites")}
        connection.close()
        remaining = [domain for domain in rows_by_shard[shard] if domain not in completed]
        path = output_dir / f"remaining-{shard}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("domain", "stratum"))
            for domain in remaining:
                writer.writerow((domain, rows_by_shard[shard][domain]))
        remaining_paths.append(path)
        remaining_domains.update(remaining)
        shard_reports.append(
            {
                "shard": shard,
                "rows": len(remaining),
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="ascii", newline="") as handle:
        for domain in sorted(remaining_domains):
            handle.write(f"{domain}\n")
    return {
        "output": str(output),
        "domains": len(remaining_domains),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "shards": shard_reports,
    }


def open_text(path: Path) -> TextIO:
    if path.suffix.casefold() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def result_fields(value: dict[str, object]) -> tuple[str, str, str]:
    result: dict[str, object] = value
    nested = value.get("results")
    if isinstance(nested, dict):
        candidate = nested.get("A")
        if isinstance(candidate, dict):
            result = candidate
    status = str(result.get("status") or "")
    timestamp = str(result.get("timestamp") or "")
    resolver = ""
    data = result.get("data")
    if isinstance(data, dict):
        resolver = str(data.get("resolver") or "")
    return status, timestamp, resolver


def read_results(path: Path) -> tuple[dict[str, tuple[str, str, str]], Counter[str]]:
    results: dict[str, tuple[str, str, str]] = {}
    statuses: Counter[str] = Counter()
    with open_text(path.resolve()) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            domain = normalize_domain(str(value.get("name") or ""))
            if not domain:
                raise ValueError(f"invalid result domain at {path}:{line_number}")
            if domain in results:
                raise ValueError(f"duplicate result domain in {path}: {domain}")
            status, timestamp, resolver = result_fields(value)
            results[domain] = (status, timestamp, resolver)
            statuses[status or "MISSING_STATUS"] += 1
    return results, statuses


def apply_consensus(
    queues: list[Path],
    databases: list[Path],
    result_paths: list[Path],
    expected_domains: int,
    summary_path: Path,
) -> dict[str, object]:
    if len(queues) != len(databases):
        raise ValueError("queue and database counts must match")
    if len(result_paths) < 2:
        raise ValueError("at least two independent resolver result files are required")
    rows_by_shard, domain_shard = load_queues(queues)
    if len(domain_shard) != expected_domains:
        raise ValueError(
            f"expected {expected_domains:,} queue domains, found {len(domain_shard):,}"
        )

    result_sets: list[dict[str, tuple[str, str, str]]] = []
    result_reports: list[dict[str, object]] = []
    queue_domains = set(domain_shard)
    for path in result_paths:
        values, statuses = read_results(path)
        missing = queue_domains - values.keys()
        extra = values.keys() - queue_domains
        if missing or extra:
            raise ValueError(
                f"result scope mismatch for {path}: missing={len(missing)} extra={len(extra)}"
            )
        result_sets.append(values)
        result_reports.append(
            {
                "path": str(path.resolve()),
                "bytes": path.resolve().stat().st_size,
                "sha256": sha256(path.resolve()),
                "rows": len(values),
                "statuses": dict(sorted(statuses.items())),
                "resolvers": sorted({value[2] for value in values.values() if value[2]}),
            }
        )

    consensus: dict[str, str] = {}
    for domain in queue_domains:
        observations = [result[domain] for result in result_sets]
        if all(status == "NXDOMAIN" for status, _, _ in observations):
            consensus[domain] = max(
                (timestamp for _, timestamp, _ in observations if timestamp),
                default=utc_now(),
            )

    reports: list[dict[str, object]] = []
    configuration = json.dumps(
        {
            "method": "independent_recursive_resolver_nxdomain_consensus",
            "required_agreement": len(result_sets),
            "result_sha256": [report["sha256"] for report in result_reports],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for shard, database in enumerate(databases):
        database = database.resolve()
        shard_rows = [
            (domain, consensus[domain])
            for domain in rows_by_shard[shard]
            if domain in consensus
        ]
        connection = sqlite3.connect(database, timeout=120)
        try:
            connection.execute("PRAGMA busy_timeout=120000")
            connection.execute(
                "CREATE TEMP TABLE dns_consensus("
                "domain TEXT PRIMARY KEY, observed_at TEXT NOT NULL) WITHOUT ROWID"
            )
            connection.executemany("INSERT INTO dns_consensus VALUES (?,?)", shard_rows)
            before = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            existing = connection.execute(
                "SELECT COUNT(*) FROM sites JOIN dns_consensus USING(domain)"
            ).fetchone()[0]
            replaceable = connection.execute(
                "SELECT COUNT(*) FROM sites JOIN dns_consensus USING(domain) "
                "WHERE status IN ('local_network_error','worker_error')"
            ).fetchone()[0]
            with connection:
                for table in ("fetches", "sitemaps", "discovered_urls", "pages"):
                    connection.execute(
                        f"DELETE FROM {table} WHERE domain IN ("
                        "SELECT sites.domain FROM sites JOIN dns_consensus USING(domain) "
                        "WHERE sites.status IN ('local_network_error','worker_error'))"
                    )
                connection.execute(
                    "DELETE FROM sites WHERE domain IN (SELECT domain FROM dns_consensus) "
                    "AND status IN ('local_network_error','worker_error')"
                )
                run_id = connection.execute(
                    "INSERT INTO crawl_runs(started_at,configuration_json) VALUES (?,?)",
                    (utc_now(), configuration),
                ).lastrowid
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sites(
                        domain,stratum,status,origin_url,final_url,started_at,finished_at,
                        error,fetch_count,page_count,sitemap_count,sitemap_url_count,
                        discovered_url_count
                    )
                    SELECT domain,?, 'no_origin', NULL, NULL, observed_at, observed_at,
                           ?,0,0,0,0,0
                    FROM dns_consensus
                    """,
                    (DNS_STRATUM, DNS_ERROR),
                )
                connection.execute(
                    "UPDATE crawl_runs SET finished_at=? WHERE run_id=?", (utc_now(), run_id)
                )
            after = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
            if quick_check != "ok":
                raise RuntimeError(f"shard {shard} quick_check failed: {quick_check}")
            reports.append(
                {
                    "shard": shard,
                    "database": str(database),
                    "consensus_domains": len(shard_rows),
                    "inserted": after - before,
                    "replaced_retryable": replaceable,
                    "preserved_existing_http": existing - replaceable,
                    "sites_after": after,
                    "quick_check": quick_check,
                }
            )
        finally:
            connection.close()

    summary: dict[str, object] = {
        "generated_at": utc_now(),
        "method": "fresh A-query NXDOMAIN consensus across independent recursive resolvers",
        "queue_domains": len(domain_shard),
        "queue_files": [
            {
                "bytes": path.resolve().stat().st_size,
                "sha256": sha256(path.resolve()),
            }
            for path in queues
        ],
        "consensus_nxdomain": len(consensus),
        "results": result_reports,
        "shards": reports,
    }
    summary_path = summary_path.resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_unresolved(
    queues: list[Path],
    databases: list[Path],
    result_paths: list[Path],
    expected_domains: int,
    summary_path: Path,
) -> dict[str, object]:
    if len(queues) != len(databases):
        raise ValueError("queue and database counts must match")
    if len(result_paths) < 2:
        raise ValueError("at least two independent resolver result files are required")
    rows_by_shard, domain_shard = load_queues(queues)
    if len(domain_shard) != expected_domains:
        raise ValueError(
            f"expected {expected_domains:,} queue domains, found {len(domain_shard):,}"
        )
    queue_domains = set(domain_shard)
    result_sets: list[dict[str, tuple[str, str, str]]] = []
    result_reports: list[dict[str, object]] = []
    for path in result_paths:
        values, statuses = read_results(path)
        if set(values) != queue_domains:
            raise ValueError(f"result scope mismatch for {path}")
        result_sets.append(values)
        result_reports.append(
            {
                "bytes": path.resolve().stat().st_size,
                "sha256": sha256(path.resolve()),
                "rows": len(values),
                "statuses": dict(sorted(statuses.items())),
                "resolvers": sorted({value[2] for value in values.values() if value[2]}),
            }
        )

    unresolved: dict[str, str] = {}
    resolvable = 0
    nxdomain_consensus = 0
    status_pairs: Counter[str] = Counter()
    for domain in queue_domains:
        observations = [result[domain] for result in result_sets]
        statuses = [status or "MISSING_STATUS" for status, _, _ in observations]
        status_pairs["|".join(statuses)] += 1
        if any(status == "NOERROR" for status in statuses):
            resolvable += 1
            continue
        if all(status == "NXDOMAIN" for status in statuses):
            nxdomain_consensus += 1
            continue
        unresolved[domain] = max(
            (timestamp for _, timestamp, _ in observations if timestamp),
            default=utc_now(),
        )
    if len(unresolved) + resolvable + nxdomain_consensus != expected_domains:
        raise RuntimeError("DNS disposition reconciliation failed")

    reports: list[dict[str, object]] = []
    configuration = json.dumps(
        {
            "method": "multi_resolver_dns_unresolved_after_retries",
            "result_sha256": [report["sha256"] for report in result_reports],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for shard, database in enumerate(databases):
        database = database.resolve()
        shard_rows = [
            (domain, unresolved[domain])
            for domain in rows_by_shard[shard]
            if domain in unresolved
        ]
        connection = sqlite3.connect(database, timeout=120)
        try:
            connection.execute("PRAGMA busy_timeout=120000")
            connection.execute(
                "CREATE TEMP TABLE dns_unresolved("
                "domain TEXT PRIMARY KEY, observed_at TEXT NOT NULL) WITHOUT ROWID"
            )
            connection.executemany("INSERT INTO dns_unresolved VALUES (?,?)", shard_rows)
            before = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            existing = connection.execute(
                "SELECT COUNT(*) FROM sites JOIN dns_unresolved USING(domain)"
            ).fetchone()[0]
            with connection:
                run_id = connection.execute(
                    "INSERT INTO crawl_runs(started_at,configuration_json) VALUES (?,?)",
                    (utc_now(), configuration),
                ).lastrowid
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sites(
                        domain,stratum,status,origin_url,final_url,started_at,finished_at,
                        error,fetch_count,page_count,sitemap_count,sitemap_url_count,
                        discovered_url_count
                    )
                    SELECT domain,?, 'dns_unresolved', NULL, NULL, observed_at, observed_at,
                           ?,0,0,0,0,0
                    FROM dns_unresolved
                    """,
                    (DNS_UNRESOLVED_STRATUM, DNS_UNRESOLVED_ERROR),
                )
                connection.execute(
                    "UPDATE crawl_runs SET finished_at=? WHERE run_id=?", (utc_now(), run_id)
                )
            after = connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
            if quick_check != "ok":
                raise RuntimeError(f"shard {shard} quick_check failed: {quick_check}")
            reports.append(
                {
                    "shard": shard,
                    "unresolved_domains": len(shard_rows),
                    "inserted": after - before,
                    "preserved_existing": existing,
                    "sites_after": after,
                    "quick_check": quick_check,
                }
            )
        finally:
            connection.close()

    summary: dict[str, object] = {
        "generated_at": utc_now(),
        "method": "no recursive resolver returned NOERROR after paced queries and one retry",
        "queue_domains": expected_domains,
        "dns_unresolved": len(unresolved),
        "resolvable_for_http": resolvable,
        "nxdomain_consensus_in_scope": nxdomain_consensus,
        "status_pairs": dict(sorted(status_pairs.items())),
        "results": result_reports,
        "shards": reports,
    }
    summary_path = summary_path.resolve()
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def aggregate_summaries(
    phase_paths: list[Path], queue_domains: int, output: Path,
    unresolved_path: Path | None = None,
) -> dict[str, object]:
    if len(phase_paths) < 1:
        raise ValueError("at least one phase summary is required")
    phases = [json.loads(path.resolve().read_text(encoding="utf-8")) for path in phase_paths]
    for index, phase in enumerate(phases):
        if phase.get("queue_domains", 0) <= 0 or phase.get("consensus_nxdomain", 0) <= 0:
            raise ValueError(f"invalid phase summary: {phase_paths[index]}")
        if len(phase.get("results", [])) < 2:
            raise ValueError(f"phase lacks independent results: {phase_paths[index]}")
        if any(
            result.get("rows") != phase["queue_domains"] for result in phase["results"]
        ):
            raise ValueError(f"phase result scope mismatch: {phase_paths[index]}")
        if any(shard.get("quick_check") != "ok" for shard in phase.get("shards", [])):
            raise ValueError(f"phase shard validation failed: {phase_paths[index]}")
    phase_scope_total = sum(phase["queue_domains"] for phase in phases)
    if phases[0]["queue_domains"] != queue_domains:
        raise ValueError("the first phase must cover the original queue")
    if any(phase["queue_domains"] > queue_domains for phase in phases[1:]):
        raise ValueError("a later phase exceeds the original queue")
    summary: dict[str, object] = {
        "generated_at": utc_now(),
        "method": "fresh A-query NXDOMAIN consensus across independent recursive resolvers; later phases cover only domains without a prior final disposition",
        "queue_domains": queue_domains,
        "consensus_nxdomain": sum(phase["consensus_nxdomain"] for phase in phases),
        "phase_query_scopes_total": phase_scope_total,
        "phases": [
            {
                "phase": index + 1,
                "queue_domains": phase["queue_domains"],
                "consensus_nxdomain": phase["consensus_nxdomain"],
                "queue_files": phase.get("queue_files", []),
                "results": [
                    {
                        key: result[key]
                        for key in ("bytes", "sha256", "rows", "statuses", "resolvers")
                    }
                    for result in phase["results"]
                ],
                "shards": [
                    {
                        key: shard[key]
                        for key in (
                            "shard", "consensus_domains", "inserted", "replaced_retryable",
                            "preserved_existing_http", "sites_after", "quick_check",
                        )
                    }
                    for shard in phase["shards"]
                ],
            }
            for index, phase in enumerate(phases)
        ],
    }
    if unresolved_path is not None:
        unresolved = json.loads(unresolved_path.resolve().read_text(encoding="utf-8"))
        if (
            unresolved.get("dns_unresolved", 0) <= 0
            or unresolved.get("queue_domains", 0) <= 0
            or unresolved.get("dns_unresolved", 0)
            + unresolved.get("resolvable_for_http", 0)
            + unresolved.get("nxdomain_consensus_in_scope", 0)
            != unresolved["queue_domains"]
            or any(shard.get("quick_check") != "ok" for shard in unresolved.get("shards", []))
        ):
            raise ValueError("invalid unresolved DNS summary")
        summary["dns_unresolved"] = unresolved["dns_unresolved"]
        summary["resolvable_for_http"] = unresolved["resolvable_for_http"]
        summary["dns_final_dispositions"] = (
            summary["consensus_nxdomain"] + unresolved["dns_unresolved"]
        )
        summary["unresolved_status_pairs"] = unresolved["status_pairs"]
        summary["unresolved_shards"] = unresolved["shards"]
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-input")
    build.add_argument("--queue", action="append", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    remaining = subparsers.add_parser("build-remaining")
    remaining.add_argument("--queue", action="append", type=Path, required=True)
    remaining.add_argument("--database", action="append", type=Path, required=True)
    remaining.add_argument("--output-dir", type=Path, required=True)
    remaining.add_argument("--output", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--queue", action="append", type=Path, required=True)
    apply.add_argument("--database", action="append", type=Path, required=True)
    apply.add_argument("--result", action="append", type=Path, required=True)
    apply.add_argument("--expected-domains", type=int, required=True)
    apply.add_argument("--summary", type=Path, required=True)
    unresolved = subparsers.add_parser("apply-unresolved")
    unresolved.add_argument("--queue", action="append", type=Path, required=True)
    unresolved.add_argument("--database", action="append", type=Path, required=True)
    unresolved.add_argument("--result", action="append", type=Path, required=True)
    unresolved.add_argument("--expected-domains", type=int, required=True)
    unresolved.add_argument("--summary", type=Path, required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--phase", action="append", type=Path, required=True)
    aggregate.add_argument("--queue-domains", type=int, required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    aggregate.add_argument("--unresolved-summary", type=Path)
    args = parser.parse_args()
    if args.command == "build-input":
        report = build_input(args.queue, args.output)
    elif args.command == "build-remaining":
        report = build_remaining_input(
            args.queue, args.database, args.output_dir, args.output
        )
    elif args.command == "apply":
        report = apply_consensus(
            args.queue,
            args.database,
            args.result,
            args.expected_domains,
            args.summary,
        )
    elif args.command == "apply-unresolved":
        report = apply_unresolved(
            args.queue,
            args.database,
            args.result,
            args.expected_domains,
            args.summary,
        )
    else:
        report = aggregate_summaries(
            args.phase, args.queue_domains, args.output, args.unresolved_summary
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
