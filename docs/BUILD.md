# Local build report

Build date: 2026-08-12

## Database

- SQLite file: `data/ro_domains.sqlite3`
- Size: 754,929,664 bytes
- SHA-256: `33c72f4e1e5cab475abd871dcdb07dc3030b87ac104e5dab791a1d457a197c1d`
- Unique registrable `.ro` domains: 746,909
- Unique observed hostnames: 753,262
- Sources catalogued: 31
- Sources imported: 11
- Domain/source associations: 1,921,764

The complete internal union includes observations from sources whose
redistribution terms are still unclear or restricted. It must not be published
as-is. The public export contains 435,574 domains supported by at least one
source marked `public_export: 1` in `sources.json`.

## Exports

| File | Data rows | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `domains_ro_public.csv.gz` | 435,574 | 4,252,440 | `527ee478adebc8b0da5b49f4067e0a1d04813259b0037bc3f48ede60e0abd444` |
| `domains_ro_public.txt.gz` | 435,574 | 1,853,699 | `01274eeee97de3abef57c74b8002e40fba2ae96ab13d955541b8f14a215a0c86` |
| `domains_ro_all_sources.csv.gz` | 746,909 | 7,002,362 | `c87aede97383c91e5ac10ea7848d6e0ed0f704ea741dfa028e5f744c4cd6c33e` |
| `domains_ro_all_sources.txt.gz` | 746,909 | 3,053,587 | `990ea5e22deda18384fa41981e5aec092cec4b6dbf8eb1c9f5a864b06d270456` |

CSV row counts exclude the header. TXT exports contain one normalized ASCII
domain per line.

## Verification

- SQLite `integrity_check`: `ok`
- Foreign-key errors: 0
- Invalid `.ro` suffixes: 0
- Invalid first/last-seen ordering: 0
- Orphan hostnames: 0
- Unfinished imports: 0
- Unit tests: 5 passed

This is an observed-domain registry, not an authoritative register of every
registered `.ro` name. The principal remaining coverage gap is direct access
to the RoTLD zone file under terms agreed with the registry.
