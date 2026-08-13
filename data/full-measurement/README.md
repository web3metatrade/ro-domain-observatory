# Complete DNS measurement release

Dataset version: `dns-snapshot-2026-08-13-full`

This snapshot covers all 746,909 normalized `.ro` candidate domains in the local
multi-source union, including candidates whose upstream redistribution status
is unclear or restricted. It contains DNS facts independently measured by this
project and preserves the source IDs that supplied each candidate domain.

## Files

| File | Rows | Description |
| --- | ---: | --- |
| `domains_ro_all_sources.csv.gz` | 746,909 | Candidate domains with actual source IDs |
| `dns_query_results.csv.gz` | 8,962,908 | Final status for each domain and 12 query types |
| `dns_records.csv.gz` | 6,174,465 | Normalized records from all DNS response sections |

Exact sizes and SHA-256 hashes are in `manifest.json`. Column definitions are
in `docs/DNS_DATASET.md`. The compressed exports are tracked directly because
each is below GitHub's 100 MiB per-file limit.

## Provenance statement

- Candidate discovery: `multi_source_union`
- DNS measurement: `independent_bulk_dns_lookup`
- Query engine: ZDNS
- Query types: A, AAAA, CNAME, MX, NS, SOA, TXT, CAA, HTTPS, SVCB, DS, DNSKEY
- Scan window: 2026-08-12 through 2026-08-13

`Bulk DNS lookup` describes how the DNS facts were generated. It does not mean
that the `.ro` namespace was enumerated by alphabetic brute force. The
`sources` column in the domain file is the authoritative candidate provenance.

The project does not claim ownership of domain names or relicense upstream
compilations. See `LICENSE-DATA.md` and `sources.json` for the scope of the data
license and source-specific status.
