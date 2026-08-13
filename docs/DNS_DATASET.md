# Consolidated DNS dataset

Snapshot: `dns-12types-1000qps-retry1-20260812T190838Z`

The five ordered ZDNS passes are consolidated by `(domain, query_type)`.
A result from a later pass replaces the earlier result for that key. Raw pass
files remain available for audit.

## `query_results`

One row per domain and requested DNS type: 8,962,908 rows for 746,909 domains
times 12 query types.

| Column | Meaning |
| --- | --- |
| `domain` | Normalized registrable `.ro` domain |
| `query_type` | Requested type: A, AAAA, CNAME, MX, NS, SOA, TXT, CAA, HTTPS, SVCB, DS or DNSKEY |
| `status` | Final ZDNS/DNS status |
| `queried_at` | Timestamp of the result that won consolidation |
| `duration_ms` | Lookup duration in milliseconds |
| `resolver` | Resolver address used for the final attempt |
| `protocol` | DNS transport, normally UDP or TCP |
| `source_pass` | Pass that supplied the final result |
| `attempt_sequence` | Ordered pass number, 1 through 5 |
| `error_class` | Normalized error category, null for definitive results |
| `authoritative` | DNS AA flag as 0/1/null |
| `authenticated` | DNS AD flag as 0/1/null |
| `recursion_available` | DNS RA flag as 0/1/null |
| `truncated` | DNS TC flag as 0/1/null |
| `record_count` | Non-EDNS records retained across all response sections |

## `dns_records`

One row per individual record from the answer, authority or additional
section: 6,174,465 rows.

| Column | Meaning |
| --- | --- |
| `domain` | Domain originally queried |
| `query_type` | Requested DNS type |
| `section` | `answer`, `authority` or `additional` |
| `owner_name` | Record owner/name |
| `record_type` | Actual record type returned |
| `ttl` | TTL in seconds |
| `value` | Primary normalized value/target/digest/key where applicable |
| `preference` | MX preference |
| `priority` | SVCB/HTTPS/SRV-style priority when present |
| `weight` | Weight when present |
| `port` | Port when present |
| `flags` | Record-specific flags |
| `tag` | CAA tag |
| `algorithm` | DNSSEC algorithm |
| `key_tag` | DS key tag |
| `digest_type` | DS digest type |
| `serial` | SOA serial |
| `refresh` | SOA refresh interval |
| `retry` | SOA retry interval |
| `expire` | SOA expire interval |
| `minimum_ttl` | SOA minimum TTL |
| `rdata_json` | Lossless type-specific RDATA fields as JSON |
| `source_pass` | Pass that supplied this record |

The SQLite database also contains a `snapshots` table with scan-level
metadata. The raw error text is retained in the original JSONL pass files;
the consolidated table stores a stable `error_class` to avoid duplicating
large repetitive messages.
