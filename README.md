# RO Domain Observatory

[![Data: ODbL 1.0](https://img.shields.io/badge/data-ODbL%201.0-blue.svg)](LICENSE-DATA.md)
[![Code: MIT](https://img.shields.io/badge/code-MIT-green.svg)](LICENSE-CODE)

Local, reproducible registry of observed `.ro` domains. The first phase only
discovers, normalizes and deduplicates domains while preserving source
provenance. DNS, HTTP, sitemap and company matching are intentionally separate
later phases.

## Principles

- A domain is never treated as belonging to a company merely because it was
  observed in a list.
- Every domain keeps source provenance and observation timestamps.
- Public exports exclude sources whose redistribution terms are unclear or
  restricted unless permission is obtained.
- Hostnames are normalized to their registrable `.ro` domain while the original
  hostname can also be retained.
- Imports are idempotent and can be resumed.

## Quick start

```powershell
python scripts/domain_registry.py init
python scripts/domain_registry.py import-cui-repo --repo "C:\path\to\cui-ro-dataset"
python scripts/domain_registry.py import-file --source majestic_million --path data/raw/majestic_million.csv --format csv --column Domain --rank-column GlobalRank
python scripts/domain_registry.py stats
python scripts/domain_registry.py verify
python scripts/domain_registry.py export --output data/exports/domains_ro.csv.gz
python scripts/domain_registry.py export --output data/exports/domains_ro.txt.gz --plain
```

The SQLite database is written to `data/ro_domains.sqlite3` by default. Raw
downloads and generated exports are ignored by Git and should be distributed as
versioned release assets or through object storage.

## Data model

- `sources`: access, licensing and redistribution status for each source.
- `domains`: unique registrable `.ro` domains.
- `hostnames`: unique observed hostnames and their registrable domains.
- `domain_sources`: one provenance record per domain and source.
- `hostname_sources`: one provenance record per hostname and source.
- `import_runs`: checksums and counters for every import execution.

See `docs/SOURCES.md` for the source inventory and `sources.json` for the
machine-readable catalog.

For OpenStreetMap PBF imports, install the optional dependency with
`python -m pip install -r requirements-optional.txt`, then run
`python scripts/domain_registry.py import-osm-pbf --path data/raw/romania-latest.osm.pbf`.

## Current build

The local 2026-08-12 build contains 746,909 unique registrable `.ro` domains
from 11 imported sources. Of these, 435,574 have provenance from at least one
source currently approved for public export. See `docs/BUILD.md` for exact
checksums and verification results.

The DNS snapshot is consolidated in `data/dns/dns_consolidated.sqlite3`, with
CSV exports in `data/exports/dns_query_results.csv.gz` and
`data/exports/dns_records.csv.gz`. See `docs/DNS_DATASET.md` for the exact
column definitions.

The repository release files under `data/public/` are filtered to domains with
at least one redistributable provenance source. Internal databases and raw
source downloads are excluded from Git.
