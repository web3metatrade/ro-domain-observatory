#!/usr/bin/env python3
"""Create deterministic resumable shards for domains without an HTTP measurement."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import zlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=3)
    parser.add_argument("--classification", default="not_measured_http")
    parser.add_argument("--stratum", default="verification")
    args = parser.parse_args()
    if args.shards < 1:
        raise ValueError("--shards must be positive")

    catalog = args.catalog.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [output_dir / f"domains-{index}.csv" for index in range(args.shards)]
    handles = [path.open("w", encoding="utf-8", newline="") for path in paths]
    writers = [csv.writer(handle, lineterminator="\n") for handle in handles]
    counts = [0] * args.shards
    try:
        for writer in writers:
            writer.writerow(("domain", "stratum"))
        with gzip.open(catalog, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"domain", "http_classification"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("catalog must contain domain and http_classification")
            for row in reader:
                if row["http_classification"] != args.classification:
                    continue
                domain = row["domain"]
                shard = zlib.crc32(domain.encode("ascii")) % args.shards
                writers[shard].writerow((domain, args.stratum))
                counts[shard] += 1
    finally:
        for handle in handles:
            handle.close()

    summary = {
        "catalog": str(catalog),
        "catalog_sha256": sha256(catalog),
        "classification": args.classification,
        "stratum": args.stratum,
        "total": sum(counts),
        "shards": [
            {
                "path": str(path),
                "rows": counts[index],
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for index, path in enumerate(paths)
        ],
    }
    (output_dir / "queue-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
