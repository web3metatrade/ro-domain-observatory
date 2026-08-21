"""Run http_pilot in fresh, memory-bounded batches until a shard is complete."""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_RETRYABLE = ("local_network_error", "worker_error")


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


class ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("page_file_usage", ctypes.c_size_t),
        ("peak_page_file_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def available_physical_mb() -> float:
    if os.name != "nt":
        return float("inf")
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return float("inf")
    return status.available_physical / (1024 * 1024)


def private_memory_mb(pid: int) -> float:
    if os.name != "nt":
        return 0.0
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCountersEx),
        ctypes.c_ulong,
    ]
    handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        return 0.0
    try:
        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        return counters.private_usage / (1024 * 1024) if ok else 0.0
    finally:
        kernel32.CloseHandle(handle)


def database_stats(
    database: Path,
    selected_domains: list[str],
    final_retry_statuses: tuple[str, ...],
    crawl_retry_statuses: tuple[str, ...] = (),
) -> tuple[int, int, dict[str, int]]:
    connection = sqlite3.connect(database, timeout=30)
    connection.execute(
        "CREATE TEMP TABLE selected_domains(domain TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    connection.executemany(
        "INSERT INTO selected_domains VALUES (?)", ((domain,) for domain in selected_domains)
    )
    statuses = dict(
        connection.execute(
            "SELECT sites.status,COUNT(*) FROM sites "
            "JOIN selected_domains USING(domain) GROUP BY sites.status"
        )
    )
    connection.close()
    stored = sum(statuses.values())
    pending = max(len(selected_domains) - stored, 0) + sum(
        statuses.get(status, 0) for status in crawl_retry_statuses
    )
    return stored, pending, statuses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=30)
    parser.add_argument("--rps", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--request-retries",
        type=int,
        default=0,
        help=(
            "Retries inside each individual HTTP request. Domain-level final retry is "
            "controlled separately and is the recommended recovery mechanism."
        ),
    )
    parser.add_argument(
        "--origin-only",
        action="store_true",
        help="Run only the fast origin-discovery stage.",
    )
    parser.add_argument(
        "--crawl-retry-status",
        action="append",
        dest="crawl_retry_statuses",
        help="Existing status to treat as pending during every normal batch.",
    )
    parser.add_argument("--memory-limit-mb", type=float, default=1200.0)
    parser.add_argument("--minimum-free-mb", type=float, default=900.0)
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--max-memory-kills", type=int, default=3)
    parser.add_argument("--idle-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--log-prefix", type=Path, required=True)
    parser.add_argument(
        "--final-retry-status",
        action="append",
        dest="final_retry_statuses",
        help=(
            "Status included in the one final retry pass. Repeat as needed. "
            "Defaults to local_network_error and worker_error."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with args.sample.open("r", encoding="utf-8", newline="") as handle:
        selected_domains = [row["domain"] for row in csv.DictReader(handle)]
    if len(selected_domains) != len(set(selected_domains)):
        raise ValueError("sample contains duplicate domains")
    selected = len(selected_domains)

    crawler = Path(__file__).with_name("http_pilot.py")
    args.log_prefix.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = args.log_prefix.with_suffix(".stdout.log")
    stderr_path = args.log_prefix.with_suffix(".stderr.log")
    memory_kills = 0
    final_retry_started = False
    final_retry_statuses = tuple(
        dict.fromkeys(args.final_retry_statuses or DEFAULT_RETRYABLE)
    )
    crawl_retry_statuses = tuple(dict.fromkeys(args.crawl_retry_statuses or ()))

    while True:
        stored, unseen, statuses = database_stats(
            args.database,
            selected_domains,
            final_retry_statuses,
            crawl_retry_statuses,
        )
        retryable = sum(statuses.get(status, 0) for status in final_retry_statuses)
        print(
            f"{utc_now()} stored={stored:,}/{selected:,} unseen={unseen:,} "
            f"retryable={retryable:,} statuses={statuses}",
            flush=True,
        )
        if unseen == 0:
            if retryable == 0:
                print(f"{utc_now()} shard complete", flush=True)
                return 0
            if final_retry_started:
                print(
                    f"{utc_now()} final retry finished; {retryable:,} persistent failures remain",
                    flush=True,
                )
                return 0
            final_retry_started = True

        while available_physical_mb() < args.minimum_free_mb:
            print(
                f"{utc_now()} waiting for free RAM: "
                f"{available_physical_mb():.0f} MB available, "
                f"{args.minimum_free_mb:.0f} MB required",
                flush=True,
            )
            time.sleep(30)

        command = [
            sys.executable,
            str(crawler),
            "--sample", str(args.sample),
            "--database", str(args.database),
            # The final pass must include every retryable domain once. Normal
            # batches stay small so each child process remains memory-bounded.
            "--batch-size", str(retryable if final_retry_started else args.batch_size),
            "--workers", str(args.workers),
            "--concurrency", str(args.concurrency),
            "--rps", str(args.rps),
            "--timeout", str(args.timeout),
            "--max-pages", "8",
            "--max-sitemaps", "20",
            "--max-sitemap-urls", "10000",
            "--retry", str(args.request_retries),
            "--final-retry-passes", "0",
            "--prevent-sleep",
        ]
        if args.origin_only:
            command.append("--origin-only")
        if final_retry_started:
            for status in final_retry_statuses:
                command.extend(("--retry-status", status))
        else:
            for status in crawl_retry_statuses:
                command.extend(("--retry-status", status))
        with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr:
            stdout.write(f"\n{utc_now()} START {' '.join(command)}\n")
            stdout.flush()
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            killed_for_memory = False
            killed_for_idle = False
            watched_paths = (args.database, Path(f"{args.database}-wal"))
            # Existing databases can be hours or days old. The idle window starts
            # with this child process, then advances whenever SQLite writes.
            last_write = time.time()
            while process.poll() is None:
                private_mb = private_memory_mb(process.pid)
                if private_mb > args.memory_limit_mb:
                    stderr.write(
                        f"{utc_now()} memory guard terminated PID {process.pid}: "
                        f"private={private_mb:.0f} MB limit={args.memory_limit_mb:.0f} MB\n"
                    )
                    stderr.flush()
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    killed_for_memory = True
                    memory_kills += 1
                    break
                newest_write = max(
                    (path.stat().st_mtime for path in watched_paths if path.exists()),
                    default=last_write,
                )
                if newest_write > last_write:
                    last_write = newest_write
                if time.time() - last_write > args.idle_timeout_seconds:
                    stderr.write(
                        f"{utc_now()} idle guard terminated PID {process.pid}: "
                        f"no database writes for {args.idle_timeout_seconds:.0f} seconds\n"
                    )
                    stderr.flush()
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    killed_for_idle = True
                    break
                time.sleep(2)

        if killed_for_memory:
            if final_retry_started:
                # Successful rows are already committed. A restarted final pass
                # therefore selects only persistent or not-yet-attempted rows.
                final_retry_started = False
            if memory_kills >= args.max_memory_kills:
                print(
                    f"{utc_now()} stopped after {memory_kills} memory-guard terminations",
                    flush=True,
                )
                return 2
            time.sleep(15)
            continue
        if killed_for_idle:
            if final_retry_started:
                final_retry_started = False
            time.sleep(15)
            continue
        if process.returncode:
            print(f"{utc_now()} crawler exited with code {process.returncode}", flush=True)
            return process.returncode
        time.sleep(args.pause_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
