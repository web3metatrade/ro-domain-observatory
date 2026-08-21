# Fully verified domain coverage — 21 August 2026

Dataset version: `http-snapshot-2026-08-21-fully-verified`

This release closes the previous HTTP measurement gap. Every domain in the
748,706-domain candidate union now has a final disposition from either the HTTP
crawler or a fresh multi-resolver DNS verification. No catalog row remains
`not_measured_http`.

## Coverage

- 748,706 unique registrable `.ro` domains;
- 247,950 available/complete HTTP measurements;
- 468,486 unavailable at measurement time;
- 1,402 robots-blocked domains;
- 3 content-decode errors;
- 30,865 domains classified separately as `dns_unresolved_at_measurement`;
- 0 domains left unmeasured.

The 343,655-domain follow-up queue was checked in full. Two independent
recursive resolvers produced 300,057 consensus NXDOMAIN observations. A second
pass retried every domain without an earlier final DNS disposition. Existing
stronger HTTP outcomes were retained instead of being overwritten by a DNS
classification. After both DNS passes, 31,240 names still had no conclusive
answer from either resolver; 375 of those already had HTTP outcomes and 30,865
receive the explicit unresolved classification in the final catalog.

`unavailable_at_measurement` and `dns_unresolved_at_measurement` are observations
for this snapshot, not claims that a domain can never become available.

## Assets

- `domain_coverage_complete.csv.gz`: canonical one-row-per-domain catalog;
- `http_sites_full.csv.gz`: domain-level HTTP or DNS disposition;
- `sitemaps_full.csv.gz`: sanitized sitemap observations;
- `company_evidence_full.csv.gz`: privacy-minimized CUI page evidence;
- `dns_nxdomain_consensus_summary.json`: resolver, retry, row-count and checksum
  audit trail;
- `manifest.json`: schemas, exact counts, byte sizes, SHA-256 hashes and
  classification semantics.

The SQLite crawl database, raw HTML, page text, titles, email addresses,
telephone numbers, JSON-LD, raw DNS response streams, raw errors and the bulk
discovered-URL graph are not release assets.

## Provenance and attribution

Candidate discovery and measurement remain separate. Per-domain discovery
sources are preserved. The union includes 1,797 valid `.ro` domains found only
in the supplied Scrape The World stage-two file. `scrape_the_world_stage2` is
attached only to domains documented by that file. Direct permission to merge
and publish it was granted to the repository maintainer by Florin Badita, CEO
of Scrape The World.

DNS and HTTP results were measured independently by this project; they were not
copied from the candidate sources.

## Reproducibility

The release was exported twice from identical inputs. All generated assets,
including the manifest, had identical byte sizes and SHA-256 hashes across both
runs. The validators also reconcile every catalog classification, verify gzip
row counts and headers, and reject privacy-sensitive columns or URL query data.
