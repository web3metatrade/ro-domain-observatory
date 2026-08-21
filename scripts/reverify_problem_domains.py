#!/usr/bin/env python3
"""Reverify every non-final DNS/HTTP disposition with independent resolvers.

The script never edits the source database used to build a queue. ``apply`` is
intended for an explicit derived copy and validates an exhaustive, disjoint
three-way disposition before changing that copy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dns_nxdomain_consensus import read_results
from export_public_http import normalize_domain


PROBLEM_STATUSES = ("no_origin", "dns_unresolved")
PARTITIONS = ("resolvable", "nxdomain", "unresolved")
DNS_NXDOMAIN_STRATUM = "dns_3_resolver_quorum_2026_08_21_corrected"
DNS_UNRESOLVED_STRATUM = "dns_3_resolver_inconclusive_2026_08_21_corrected"
OUTPUT_COLUMNS = ("domain", "stratum", "observed_at", "dns_observations")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_queue(database: Path, output: Path, input_path: Path, summary: Path) -> dict:
    database = database.resolve()
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=120)
    try:
        rows = connection.execute(
            "SELECT domain,stratum,status,COALESCE(error,'') FROM sites "
            "WHERE status IN (?,?) ORDER BY domain",
            PROBLEM_STATUSES,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        statuses: Counter[str] = Counter()
        strata: Counter[str] = Counter()
        count = 0
        with output.open("w", encoding="utf-8", newline="") as queue, input_path.open(
            "w", encoding="ascii", newline=""
        ) as zdns_input:
            writer = csv.writer(queue, lineterminator="\n")
            writer.writerow(("domain", "prior_stratum", "prior_status", "prior_error"))
            for domain, stratum, status, error in rows:
                normalized = normalize_domain(domain)
                if not normalized or not normalized.endswith(".ro"):
                    raise ValueError(f"invalid problem domain in database: {domain!r}")
                writer.writerow((normalized, stratum or "", status, error))
                zdns_input.write(f"{normalized}\n")
                statuses[status] += 1
                strata[stratum or ""] += 1
                count += 1
    finally:
        connection.close()
    report = {
        "generated_at": utc_now(),
        "source_database": str(database),
        "source_database_bytes": database.stat().st_size,
        "problem_statuses": list(PROBLEM_STATUSES),
        "domains": count,
        "prior_statuses": dict(sorted(statuses.items())),
        "prior_strata": dict(sorted(strata.items())),
        "queue": {"path": str(output.resolve()), "bytes": output.stat().st_size, "sha256": sha256(output)},
        "zdns_input": {"path": str(input_path.resolve()), "bytes": input_path.stat().st_size, "sha256": sha256(input_path)},
    }
    write_json(summary, report)
    return report


def read_queue_domains(path: Path) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "domain" not in reader.fieldnames:
            raise ValueError(f"queue has no domain column: {path}")
        for line, row in enumerate(reader, 2):
            domain = normalize_domain(row["domain"])
            if not domain or not domain.endswith(".ro"):
                raise ValueError(f"invalid domain at {path}:{line}")
            if domain in seen:
                raise ValueError(f"duplicate queue domain: {domain}")
            seen.add(domain)
            domains.append(domain)
    return domains


def slice_results(queue: Path, source: Path, output: Path) -> dict:
    """Create an exact-scope JSONL result file without interpreting responses."""
    scope = set(read_queue_domains(queue))
    found: set[str] = set()
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8") as input_handle, output.open(
        "w", encoding="utf-8", newline=""
    ) as output_handle:
        for line_number, line in enumerate(input_handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {source}:{line_number}") from exc
            domain = normalize_domain(str(value.get("name") or ""))
            if domain not in scope:
                continue
            if domain in found:
                raise ValueError(f"duplicate result domain in {source}: {domain}")
            output_handle.write(line.rstrip("\r\n") + "\n")
            found.add(domain)
    missing = scope - found
    if missing:
        raise ValueError(f"source results missing {len(missing):,} queue domains")
    return {
        "queue_domains": len(scope),
        "output": str(output.resolve()),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
    }


def split_input(queue: Path, output_dir: Path, shards: int) -> dict:
    if shards < 1:
        raise ValueError("shards must be positive")
    domains = read_queue_domains(queue)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [output_dir / f"input-{index}.txt" for index in range(shards)]
    handles = [path.open("w", encoding="ascii", newline="") for path in paths]
    counts = [0] * shards
    try:
        for domain in domains:
            index = zlib.crc32(domain.encode("ascii")) % shards
            handles[index].write(domain + "\n")
            counts[index] += 1
    finally:
        for handle in handles:
            handle.close()
    return {
        "queue_domains": len(domains),
        "shards": [
            {
                "path": str(path.resolve()),
                "rows": counts[index],
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for index, path in enumerate(paths)
        ],
    }


def merge_result_shards(queue: Path, sources: list[Path], output: Path) -> dict:
    scope = set(read_queue_domains(queue))
    found: set[str] = set()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as output_handle:
        for source in sources:
            with source.open("r", encoding="utf-8") as input_handle:
                for line_number, line in enumerate(input_handle, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"invalid JSON at {source}:{line_number}") from exc
                    domain = normalize_domain(str(value.get("name") or ""))
                    if domain not in scope:
                        raise ValueError(f"result outside queue scope: {domain}")
                    if domain in found:
                        raise ValueError(f"duplicate result across shards: {domain}")
                    found.add(domain)
                    output_handle.write(line.rstrip("\r\n") + "\n")
    missing = scope - found
    if missing:
        raise ValueError(f"result shards are missing {len(missing):,} queue domains")
    return {
        "queue_domains": len(scope),
        "sources": len(sources),
        "output": str(output.resolve()),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
    }


def analyze(
    queue: Path,
    results: list[Path],
    output_dir: Path,
    expected: int,
    quorum: int,
    authoritative_result: Path | None = None,
) -> dict:
    if len(results) < 2 or not 2 <= quorum <= len(results):
        raise ValueError("quorum must be between 2 and the number of resolver results")
    domains = read_queue_domains(queue)
    if len(domains) != expected:
        raise ValueError(f"expected {expected:,} queue rows, found {len(domains):,}")
    scope = set(domains)
    result_sets = []
    result_reports = []
    authoritative_index = None
    for path in results:
        values, statuses = read_results(path)
        missing = scope - values.keys()
        extra = values.keys() - scope
        if missing or extra:
            raise ValueError(
                f"result scope mismatch for {path}: missing={len(missing)} extra={len(extra)}"
            )
        result_sets.append(values)
        if authoritative_result is not None and path.resolve() == authoritative_result.resolve():
            authoritative_index = len(result_sets) - 1
        resolver_values = sorted({row[2] for row in values.values() if row[2]})
        result_reports.append(
            {
                "path": str(path.resolve()),
                "rows": len(values),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "statuses": dict(sorted(statuses.items())),
                "resolver_count": len(resolver_values),
                "resolvers": (
                    resolver_values
                    if len(resolver_values) <= 20
                    else [f"multiple_authoritative_servers:{len(resolver_values)}"]
                ),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    handles = {}
    writers = {}
    counts: Counter[str] = Counter()
    combinations: Counter[str] = Counter()
    try:
        for name in PARTITIONS:
            handle = (output_dir / f"{name}.csv").open("w", encoding="utf-8", newline="")
            handles[name] = handle
            writers[name] = csv.writer(handle, lineterminator="\n")
            writers[name].writerow(OUTPUT_COLUMNS)
        for domain in sorted(domains):
            observations = [values[domain] for values in result_sets]
            statuses = [row[0] or "MISSING_STATUS" for row in observations]
            combinations["|".join(statuses)] += 1
            if "NOERROR" in statuses:
                disposition = "resolvable"
                stratum = "dns_3_resolver_any_noerror_2026_08_21_corrected"
            elif (
                authoritative_index is not None
                and statuses[authoritative_index] == "NXDOMAIN"
            ) or statuses.count("NXDOMAIN") >= quorum:
                disposition = "nxdomain"
                stratum = DNS_NXDOMAIN_STRATUM
            else:
                disposition = "unresolved"
                stratum = DNS_UNRESOLVED_STRATUM
            observed_at = max((row[1] for row in observations if row[1]), default=utc_now())
            writers[disposition].writerow(
                (domain, stratum, observed_at, "|".join(statuses))
            )
            counts[disposition] += 1
    finally:
        for handle in handles.values():
            handle.close()
    if sum(counts.values()) != expected:
        raise RuntimeError("DNS disposition reconciliation failed")
    if authoritative_result is not None and authoritative_index is None:
        raise ValueError("authoritative result must also be supplied as --result")
    files = {
        name: {
            "rows": counts[name],
            "bytes": (output_dir / f"{name}.csv").stat().st_size,
            "sha256": sha256(output_dir / f"{name}.csv"),
        }
        for name in PARTITIONS
    }
    report = {
        "generated_at": utc_now(),
        "method": (
            "any NOERROR is resolvable; NXDOMAIN requires either an iterative "
            "authoritative result or recursive-resolver quorum; all other "
            "combinations are inconclusive"
        ),
        "queue_domains": expected,
        "nxdomain_quorum": quorum,
        "authoritative_iterative_result_sha256": (
            result_reports[authoritative_index]["sha256"]
            if authoritative_index is not None
            else None
        ),
        "partitions": files,
        "status_combinations": dict(sorted(combinations.items())),
        "results": result_reports,
    }
    write_json(output_dir / "summary.json", report)
    return report


def load_partition(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            domain = normalize_domain(row["domain"])
            if domain in rows:
                raise ValueError(f"duplicate partition domain: {domain}")
            rows[domain] = row
    return rows


def merge_phases(phase_dirs: list[Path], output_dir: Path, expected: int) -> dict:
    if len(phase_dirs) < 2:
        raise ValueError("at least two phases are required")
    raw_phase_summaries = [
        json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        for directory in phase_dirs
    ]
    phase_summaries = [
        {
            "phase": index,
            "queue_domains": phase["queue_domains"],
            "nxdomain_quorum": phase["nxdomain_quorum"],
            "authoritative_iterative_result_sha256": phase.get(
                "authoritative_iterative_result_sha256"
            ),
            "partitions": phase["partitions"],
            "status_combinations": phase["status_combinations"],
            "results": [
                {
                    **{
                        key: result[key]
                        for key in ("rows", "bytes", "sha256", "statuses", "resolvers")
                    },
                    "resolver_count": result.get(
                        "resolver_count", len(result.get("resolvers", []))
                    ),
                }
                for result in phase["results"]
            ],
            "shards": [],
        }
        for index, phase in enumerate(raw_phase_summaries, 1)
    ]
    final = {
        name: load_partition(phase_dirs[0] / f"{name}.csv")
        for name in PARTITIONS
    }
    first_scope = set().union(*(set(rows) for rows in final.values()))
    if sum(map(len, final.values())) != len(first_scope) or len(first_scope) != expected:
        raise ValueError("phase 1 partitions are not disjoint and exhaustive")
    retry_scopes = []
    for index, phase_dir in enumerate(phase_dirs[1:], 2):
        phase = {
            name: load_partition(phase_dir / f"{name}.csv")
            for name in PARTITIONS
        }
        phase_scope = set().union(*(set(rows) for rows in phase.values()))
        if sum(map(len, phase.values())) != len(phase_scope):
            raise ValueError(f"phase {index} partitions overlap")
        if phase_scope != set(final["unresolved"]):
            raise ValueError(
                f"phase {index} must exactly cover the previous unresolved partition"
            )
        retry_scopes.append(len(phase_scope))
        final = {
            "resolvable": final["resolvable"] | phase["resolvable"],
            "nxdomain": final["nxdomain"] | phase["nxdomain"],
            "unresolved": phase["unresolved"],
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, rows in final.items():
        path = output_dir / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for domain in sorted(rows):
                writer.writerow(rows[domain])
        files[name] = {"rows": len(rows), "bytes": path.stat().st_size, "sha256": sha256(path)}
    if sum(file["rows"] for file in files.values()) != expected:
        raise RuntimeError("final dispositions do not reconcile")
    report = {
        "generated_at": utc_now(),
        "method": "recursive resolver observations, iterative authoritative verification, then a final DoH retry of only inconclusive domains",
        "queue_domains": expected,
        "original_problem_domains": expected,
        "retry_phase_scopes": retry_scopes,
        "phase_query_scopes_total": expected + sum(retry_scopes),
        "consensus_nxdomain": len(final["nxdomain"]),
        "dns_unresolved": len(final["unresolved"]),
        "resolvable_for_http": len(final["resolvable"]),
        "dns_final_dispositions": len(final["nxdomain"]) + len(final["unresolved"]),
        "partitions": files,
        "phases": phase_summaries,
    }
    write_json(output_dir / "summary.json", report)
    return report


def merge(phase1: Path, phase2: Path, output_dir: Path, expected: int) -> dict:
    return merge_phases([phase1, phase2], output_dir, expected)


def apply(database: Path, disposition_dir: Path, expected: int, summary: Path) -> dict:
    partitions = {
        name: load_partition(disposition_dir / f"{name}.csv") for name in PARTITIONS
    }
    scope = set().union(*(set(rows) for rows in partitions.values()))
    if sum(map(len, partitions.values())) != len(scope) or len(scope) != expected:
        raise ValueError("final partitions are not disjoint and exhaustive")
    database = database.resolve()
    connection = sqlite3.connect(database, timeout=300)
    try:
        connection.execute("PRAGMA busy_timeout=300000")
        connection.execute(
            "CREATE TEMP TABLE dispositions(domain TEXT PRIMARY KEY, disposition TEXT NOT NULL, "
            "stratum TEXT NOT NULL, observed_at TEXT NOT NULL) WITHOUT ROWID"
        )
        for name, rows in partitions.items():
            connection.executemany(
                "INSERT INTO dispositions VALUES (?,?,?,?)",
                (
                    (domain, name, row["stratum"], row["observed_at"])
                    for domain, row in rows.items()
                ),
            )
        before = dict(connection.execute("SELECT status,COUNT(*) FROM sites GROUP BY status"))
        matched = connection.execute(
            "SELECT COUNT(*) FROM sites JOIN dispositions USING(domain)"
        ).fetchone()[0]
        with connection:
            for table in ("fetches", "sitemaps", "discovered_urls", "pages"):
                connection.execute(
                    f"DELETE FROM {table} WHERE domain IN (SELECT domain FROM dispositions)"
                )
            connection.execute("DELETE FROM sites WHERE domain IN (SELECT domain FROM dispositions)")
            connection.execute(
                """
                INSERT INTO sites(
                    domain,stratum,status,origin_url,final_url,started_at,finished_at,error,
                    fetch_count,page_count,sitemap_count,sitemap_url_count,discovered_url_count
                )
                SELECT domain,stratum,
                       CASE disposition WHEN 'nxdomain' THEN 'dns_nxdomain' ELSE 'dns_unresolved' END,
                       NULL,NULL,observed_at,observed_at,
                       CASE disposition WHEN 'nxdomain' THEN 'dns_nxdomain_consensus'
                            ELSE 'dns_unresolved_after_retries' END,
                       0,0,0,0,0
                FROM dispositions WHERE disposition IN ('nxdomain','unresolved')
                """
            )
        after = dict(connection.execute("SELECT status,COUNT(*) FROM sites GROUP BY status"))
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"derived database quick_check failed: {quick_check}")
    finally:
        connection.close()
    report = {
        "generated_at": utc_now(),
        "database": str(database),
        "database_bytes": database.stat().st_size,
        "problem_domains": expected,
        "matched_prior_site_rows": matched,
        "queued_for_http": len(partitions["resolvable"]),
        "dns_nxdomain": len(partitions["nxdomain"]),
        "dns_unresolved": len(partitions["unresolved"]),
        "statuses_before": before,
        "statuses_after": after,
        "quick_check": quick_check,
    }
    write_json(summary, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-queue")
    build.add_argument("--database", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--zdns-input", type=Path, required=True)
    build.add_argument("--summary", type=Path, required=True)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--queue", type=Path, required=True)
    analysis.add_argument("--result", type=Path, action="append", required=True)
    analysis.add_argument("--output-dir", type=Path, required=True)
    analysis.add_argument("--expected", type=int, required=True)
    analysis.add_argument("--nxdomain-quorum", type=int, default=2)
    analysis.add_argument(
        "--authoritative-result",
        type=Path,
        help="Iterative ZDNS result whose NXDOMAIN answer is authoritative.",
    )
    sliced = sub.add_parser("slice-results")
    sliced.add_argument("--queue", type=Path, required=True)
    sliced.add_argument("--source", type=Path, required=True)
    sliced.add_argument("--output", type=Path, required=True)
    split = sub.add_parser("split-input")
    split.add_argument("--queue", type=Path, required=True)
    split.add_argument("--output-dir", type=Path, required=True)
    split.add_argument("--shards", type=int, required=True)
    result_merge = sub.add_parser("merge-result-shards")
    result_merge.add_argument("--queue", type=Path, required=True)
    result_merge.add_argument("--source", type=Path, action="append", required=True)
    result_merge.add_argument("--output", type=Path, required=True)
    merged = sub.add_parser("merge")
    merged.add_argument("--phase1", type=Path, required=True)
    merged.add_argument("--phase2", type=Path, required=True)
    merged.add_argument("--output-dir", type=Path, required=True)
    merged.add_argument("--expected", type=int, required=True)
    merge_many = sub.add_parser("merge-many")
    merge_many.add_argument("--phase", type=Path, action="append", required=True)
    merge_many.add_argument("--output-dir", type=Path, required=True)
    merge_many.add_argument("--expected", type=int, required=True)
    applied = sub.add_parser("apply")
    applied.add_argument("--database", type=Path, required=True)
    applied.add_argument("--disposition-dir", type=Path, required=True)
    applied.add_argument("--expected", type=int, required=True)
    applied.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build-queue":
        report = build_queue(args.database, args.output, args.zdns_input, args.summary)
    elif args.command == "analyze":
        report = analyze(
            args.queue,
            args.result,
            args.output_dir,
            args.expected,
            args.nxdomain_quorum,
            args.authoritative_result,
        )
    elif args.command == "slice-results":
        report = slice_results(args.queue, args.source, args.output)
    elif args.command == "split-input":
        report = split_input(args.queue, args.output_dir, args.shards)
    elif args.command == "merge-result-shards":
        report = merge_result_shards(args.queue, args.source, args.output)
    elif args.command == "merge":
        report = merge(args.phase1, args.phase2, args.output_dir, args.expected)
    elif args.command == "merge-many":
        report = merge_phases(args.phase, args.output_dir, args.expected)
    else:
        report = apply(args.database, args.disposition_dir, args.expected, args.summary)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
