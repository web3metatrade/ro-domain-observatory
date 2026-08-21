# Complete HTTP measurement snapshot

Dataset version: `http-snapshot-2026-08-19-full-measurement`

This release is the complete privacy-minimized projection of the local HTTP
crawl performed from 13 through 19 August 2026. It covers all 405,051 domains
processed by the crawler. The HTTP responses and sitemap observations were
measured independently by this project.

Candidate discovery and HTTP measurement are separate provenance layers. Each
domain in `http_sites_full.csv.gz` carries its actual comma-separated
`discovery_sources`. `scrape_the_world_stage2` is added only when the normalized
domain occurs in the source file supplied by Scrape The World. Direct permission
to merge that file into this public repository was granted by Florin Badita,
CEO of Scrape The World, and is held by the repository maintainer.

## Coverage

Metric | Count
--- | ---:
HTTP domains | 405,051
Previously published public-provenance intersection | 267,863
Additional HTTP domains in this complete release | 137,188
Scrape The World stage-two domains present in the crawl | 7,696
Scrape The World stage-two domains among the additional 137,188 | 1,333
Complete site crawls | 244,076
No reachable origin | 159,578
Robots-blocked domains | 1,394
Content-decode errors | 3

The remaining additional domains retain their recorded discovery attribution;
they are not relabeled as Scrape The World. In the current registry, that group
is predominantly sourced from the CT-derived `all_domains_ct` candidate list.

## Release files

File | Rows | Bytes | SHA-256
--- | ---: | ---: | ---
`http_sites_full.csv.gz` | 405,051 | 7,777,895 | `0ab4de5179c4c3e880a48b335596b97bf90dad85b612f73c638bb30719e2781f`
`sitemaps_full.csv.gz` | 1,322,812 | 13,221,991 | `56503d6a5ad6f9f15d1440a78f74a6439e6dc2d274cfd96b4d9f047f15afaa2a`
`company_evidence_full.csv.gz` | 86,971 | 5,320,807 | `dad73d31cec46a5449316c829f2da259eb2ac126df8c3124f4d6eaa98c576741`

The release also contains `manifest.json`, with source identities, schemas,
row counts, file hashes, coverage reconciliation and privacy rules.

## Domain-level schema

`http_sites_full.csv.gz` contains one row per crawled domain. Its columns are:

Column | Meaning
--- | ---
`domain` | Normalized registrable `.ro` domain
`discovery_sources` | Actual candidate-list source IDs, comma-separated
`stratum` | Crawl-priority stratum used by the scheduler
`status` | Final site-level crawl status
`origin_url` | Sanitized origin selected for crawling
`final_url` | Sanitized final URL after redirects
`started_at` | Site crawl start timestamp
`finished_at` | Site crawl completion timestamp
`error_class` | Stable normalized error class
`fetch_count` | HTTP requests recorded for the domain
`page_count` | Parsed pages retained for the domain
`sitemap_count` | Sitemap responses recorded for the domain
`sitemap_url_count` | URLs enumerated from sitemap processing
`discovered_url_count` | Total URLs discovered locally; URLs are not exported

The sitemap and company-evidence schemas are identical to those documented in
`HTTP_DATASET.md`, except that their filenames use the `_full` suffix.

## Privacy and interpretation

The release excludes raw HTML, page text, titles, email addresses, telephone
numbers, JSON-LD, raw errors and the bulk-discovered URL graph. URL credentials,
query strings and fragments are removed. A CUI evidence row is a candidate
signal from a page, not a claim of domain ownership.

## Reproduction

```powershell
python scripts/export_full_http.py `
  --http-db C:\path\to\http_consolidated.sqlite3 `
  --source-summary C:\path\to\http_consolidated.summary.json `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --previous-public-domains data\public\domains_ro_public.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies `
  --output-dir C:\path\to\release

python scripts/validate_full_http_release.py `
  --release-dir C:\path\to\release `
  --all-domains data\full-measurement\domains_ro_all_sources.csv.gz `
  --scrape-the-world-stage2 C:\path\to\websites-stage2-autocandidates.csv `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies
```

Repeated exports from identical inputs use deterministic gzip metadata and must
produce the hashes listed above.
