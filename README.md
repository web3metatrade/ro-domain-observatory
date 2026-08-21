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
- The curated public export excludes sources whose redistribution terms are
  unclear or restricted unless permission is obtained.
- Complete DNS measurement releases may cover the full candidate union. They
  preserve candidate-source provenance and distinguish discovery inputs from
  DNS observations collected independently by this project.
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

The repository files under `data/public/` are filtered to domains with at least
one redistributable provenance source. The complete independently measured DNS
snapshot, including its compressed exports and checksum manifest, is under
`data/full-measurement/`.

The complete release was produced by a **bulk DNS lookup over a multi-source
candidate list**. It was not produced by alphabetically brute-forcing the `.ro`
namespace. The domain-candidate file retains the actual source IDs; DNS answers
and statuses were measured directly by this project.

The privacy-minimized HTTP snapshot from 13–19 August 2026 covers 267,863
public-provenance domains, 909,683 unique sanitized sitemap observations and
70,237 single-active-CUI page-evidence rows. Large generated files are
distributed as GitHub Release assets instead of being added to Git history.
See `docs/HTTP_DATASET.md` for schemas, hashes, privacy exclusions and the exact
reproduction commands.

The fully verified 21 August release covers the complete 748,706-domain union.
Every domain has an HTTP result or a fresh multi-resolver DNS disposition: the
canonical catalog contains 247,950 available, 468,486 unavailable, 1,402
robots-blocked, 3 content-decode-error and 30,865 explicitly DNS-unresolved
rows. No row remains `not_measured_http`. The privacy-minimized assets contain
1,341,391 sitemap observations and 89,523 single-active-CUI evidence rows.

The release retains actual candidate discovery sources and adds Scrape The
World attribution only where the supplied stage-two file documents it. Large
privacy-minimized exports are GitHub Release assets; raw HTML, contact data and
the bulk URL graph are excluded. See `docs/FULL_HTTP_DATASET.md` and the
[21 August release notes](docs/releases/HTTP_SNAPSHOT_2026-08-21_FULLY_VERIFIED.md).
