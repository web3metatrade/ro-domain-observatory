# Fully verified HTTP and DNS disposition snapshot

Dataset version: `http-snapshot-2026-08-21-fully-verified`

This is the complete privacy-minimized projection of the `.ro` domain
measurement performed from 13 through 21 August 2026. It contains every domain
in the 748,706-domain candidate union. Each domain has either an HTTP crawl
outcome or a fresh multi-resolver DNS disposition; none remains unmeasured.

Candidate discovery and measurement are separate provenance layers. Each row
retains its actual `discovery_sources`. `scrape_the_world_stage2` is added only
when the normalized domain occurs in the source file supplied by Scrape The
World. Direct permission to merge and publish that file was granted by Florin
Badita, CEO of Scrape The World, and is held by the repository maintainer.

## Coverage

Classification | Domains
--- | ---:
`available_at_measurement` | 247,950
`unavailable_at_measurement` | 468,486
`robots_blocked` | 1,402
`content_decode_error` | 3
`dns_unresolved_at_measurement` | 30,865
`not_measured_http` | 0
**Total** | **748,706**

The candidate union consists of the 746,909-domain base build plus 1,797 valid
`.ro` domains found only in the supplied Scrape The World stage-two file.

The site-level database status totals are the same counts under their crawler
names: `complete`, `no_origin`, `robots_blocked`, `content_decode_error` and
`dns_unresolved` respectively.

## Verification of the previous gap

The prior complete catalog contained 343,655 domains without an HTTP
classification. A deterministic queue covered all of them. Fresh A queries
were sent to the Cloudflare and Google recursive resolvers, and NXDOMAIN was
accepted only where both independently agreed.

Metric | Domains
--- | ---:
Follow-up queue | 343,655
Consensus NXDOMAIN observations across both phases | 300,057
No conclusive DNS answer after the retry phase | 31,240
Unresolved names with an existing HTTP outcome retained | 375
Final `dns_unresolved` rows | 30,865

The second DNS phase rechecked every domain without an earlier final
disposition. Stronger existing HTTP outcomes were never overwritten by DNS.
Resolvable names were crawled with one final retry after the complete queue had
been traversed. Every HTTP shard passed `PRAGMA quick_check`, had its expected
domain count and contained zero remaining retryable rows.

The release asset `dns_nxdomain_consensus_summary.json` records queue scopes,
resolver endpoints, status distributions, byte sizes and SHA-256 hashes for the
two DNS phases. Raw DNS response streams are intentionally excluded.

## Release files

File | Rows | Bytes | SHA-256
--- | ---: | ---: | ---
`http_sites_full.csv.gz` | 748,706 | 11,117,840 | `6edb046e68b0b6f22bf59ec869e455ced5668e8c0bdd4ce9b44735f1641dae9d`
`sitemaps_full.csv.gz` | 1,341,391 | 13,400,897 | `a7f89b7420123d53ccf87fc7e905a5c7cf8e58e6bcf6942715fa66120273d06c`
`company_evidence_full.csv.gz` | 89,523 | 5,478,019 | `033b3e412aea3c74728c4fc3560e86d9df04eec1ebb5d852e74b1b1b384b52cb`
`dns_nxdomain_consensus_summary.json` | 331,297 dispositions | 5,798 | `722b30d70bd26ada3db00c12395138e84f33bf41676092c34cca8ddc3f8710c3`

The complete-catalog export adds `domain_coverage_complete.csv.gz`: 748,706
rows, 16,490,016 bytes, SHA-256
`079e83397d3d00568279ee5f76bb0aa13cff4926cb86840aa059ea9838e25bb8`.
Each release also contains a manifest with schemas, counts, byte sizes, hashes
and semantics.

The release manifest is authoritative for exact asset identities. Every export
was generated twice from identical inputs and required byte-identical output.

## Domain-level schema

`http_sites_full.csv.gz` contains one row per domain:

Column | Meaning
--- | ---
`domain` | Normalized registrable `.ro` domain
`discovery_sources` | Actual candidate-list source IDs, comma-separated
`stratum` | HTTP scheduler stratum or DNS verification stratum
`status` | Final site-level disposition
`origin_url` | Sanitized origin selected for HTTP crawling, when applicable
`final_url` | Sanitized final URL after redirects, when applicable
`started_at` | Measurement start timestamp
`finished_at` | Measurement completion timestamp
`error_class` | Stable normalized error or DNS disposition class
`fetch_count` | HTTP requests recorded for the domain
`page_count` | Parsed pages retained for the domain
`sitemap_count` | Sitemap responses recorded for the domain
`sitemap_url_count` | URLs enumerated from sitemap processing
`discovered_url_count` | URLs discovered locally; the URL graph is not exported

The sitemap and company-evidence schemas are the `_full` variants documented in
`HTTP_DATASET.md`.

## Complete coverage catalog

`domain_coverage_complete.csv.gz` joins candidate provenance, historical
A/AAAA/NS status, delegation class and the final HTTP/DNS disposition without
dropping unavailable or unresolved domains.

`http_crawl_state=dns_verified_no_origin` identifies consensus-NXDOMAIN rows
that do not have a stronger HTTP outcome. `dns_verified_unresolved` means that
both resolvers and the retry phase failed to produce a conclusive answer; it is
kept separate from `unavailable_at_measurement`. These are time-bounded
measurement results, not claims about permanent availability.

## Privacy and interpretation

The release excludes raw HTML, page text, titles, email addresses, telephone
numbers, JSON-LD, raw errors and the bulk discovered-URL graph. URL credentials,
query strings and fragments are removed. A CUI evidence row is a candidate
signal found on a page, not a claim of domain ownership.

## Reproduction

```powershell
python scripts/consolidate_http.py `
  --output C:\path\to\http_consolidated.sqlite3 `
  --expected-domains 748706 `
  --source "prior_validated=C:\path\to\prior_http_consolidated.sqlite3" `
  --source "verification_0=C:\path\to\http-shard-0.sqlite3" `
  --source "verification_1=C:\path\to\http-shard-1.sqlite3" `
  --source "verification_2=C:\path\to\http-shard-2.sqlite3"

python scripts/export_full_http.py `
  --http-db C:\path\to\http_consolidated.sqlite3 `
  --source-summary C:\path\to\http_consolidated.summary.json `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --previous-public-domains data\public\domains_ro_public.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --dns-consensus-summary C:\path\to\dns-consensus-summary.json `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies `
  --dataset-version http-snapshot-2026-08-21-fully-verified `
  --output-dir C:\path\to\full-release

python scripts/export_complete_domain_catalog.py `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --dns-query-results data\full-measurement\dns_query_results.csv.gz `
  --http-db C:\path\to\http_consolidated.sqlite3 `
  --full-release-dir C:\path\to\full-release `
  --dataset-version http-snapshot-2026-08-21-fully-verified `
  --output-dir C:\path\to\complete-release

python scripts/validate_full_http_release.py `
  --release-dir C:\path\to\complete-release `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies

python scripts/validate_complete_domain_catalog.py `
  --release-dir C:\path\to\complete-release `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --dns-query-results data\full-measurement\dns_query_results.csv.gz `
  --http-db C:\path\to\http_consolidated.sqlite3
```

See `docs/releases/HTTP_SNAPSHOT_2026-08-21_FULLY_VERIFIED.md` for the concise
release summary.
