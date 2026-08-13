# Public data release

This directory contains only domains supported by at least one source marked
`public_export: 1` in the provenance catalog. It intentionally excludes
observations available solely from sources whose redistribution terms are
unclear, restricted or research-only.

| File | Rows | Description |
| --- | ---: | --- |
| `domains_ro_public.csv.gz` | 435,574 | Unique `.ro` domains and public-source provenance |
| `dns_query_results_public.csv.gz` | 5,226,888 | One final result for each public domain and each of 12 DNS query types |
| `dns_records_public.csv.gz` | 4,109,974 | Individual DNS records from answer, authority and additional sections |

Exact byte sizes and SHA-256 checksums are recorded in `manifest.json`.
Column definitions are documented in `docs/DNS_DATASET.md`.

The DNS observations are a point-in-time measurement from 2026-08-12 through
2026-08-13. A missing record is not proof that a domain never had or will not
later have that record.
