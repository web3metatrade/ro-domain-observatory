# Public HTTP and sitemap snapshot

Dataset version: `http-snapshot-2026-08-19-public`

This release is a privacy-minimized projection of the local HTTP crawl performed
from 13 through 19 August 2026. It covers only domains supported by at least one
source marked for public redistribution in `sources.json`.

The source crawl processed 405,051 domains. Its intersection with the public
provenance set contains 267,863 domains. The original 15.9 GB SQLite database,
raw HTML, page text and the bulk URL-discovery graph are not published.

## Release files

File | Rows | Bytes | SHA-256
--- | ---: | ---: | ---
`http_sites_public.csv.gz` | 267,863 | 4,714,624 | `59a698e3baa728f31c4406c41fb741bd5fe5abce7d093b145554cb1eefce75cf`
`sitemaps_public.csv.gz` | 909,683 | 9,322,472 | `4fd0c6627c0105c8c0b989dd94dc2b26dba548847b2f4a7bc0113f660eaf1aaf`
`company_evidence_public.csv.gz` | 70,237 | 4,332,460 | `6795d19e4f7ffc903ca5f4f4b9a0e7e34e8b3988591d60ccbf238b4b10f2641c`

The release also contains `manifest.json`, which is the machine-readable source
of file schemas, row counts, hashes, source-database identity and privacy rules.

## `http_sites_public.csv.gz`

One row per crawled public-provenance domain.

Column | Meaning
--- | ---
`domain` | Normalized registrable `.ro` domain
`stratum` | Crawl-priority stratum used by the local scheduler
`status` | Final site-level crawl status
`origin_url` | Sanitized origin selected for crawling
`final_url` | Sanitized final URL after redirects
`started_at` | Site crawl start timestamp
`finished_at` | Site crawl completion timestamp
`error_class` | Stable normalized error class; raw errors are excluded
`fetch_count` | HTTP requests recorded for the domain
`page_count` | Parsed pages retained for the domain
`sitemap_count` | Sitemap responses recorded for the domain
`sitemap_url_count` | URLs enumerated from sitemap processing
`discovered_url_count` | Total URLs discovered locally; the URLs themselves are excluded

Site status counts are:

- `complete`: 163,982;
- `no_origin`: 103,075;
- `robots_blocked`: 803;
- `content_decode_error`: 3.

## `sitemaps_public.csv.gz`

One row per unique sanitized sitemap observation. Query strings, fragments and
credentials are removed from both requested and final URLs. Internal crawler
sentinels outside the HTTP 100–599 range are not exported as HTTP statuses.

Column | Meaning
--- | ---
`domain` | Domain for which the sitemap was tested or discovered
`url` | Sanitized sitemap URL
`final_url` | Sanitized URL after redirects
`http_status` | Public HTTP status, empty when unavailable or invalid
`kind` | Controlled sitemap format/category
`url_count` | URLs reported by the parsed sitemap
`child_count` | Child sitemaps reported by an index
`depth` | Sitemap traversal depth
`truncated` | `1` when crawler limits truncated processing, otherwise `0`
`error_class` | Stable normalized error class

## `company_evidence_public.csv.gz`

This is a candidate-evidence table, not a statement of website ownership. A row
is included only when a non-soft-404 2xx/3xx page contains exactly one valid CUI
that is present in the active-company reference snapshot.

Column | Meaning
--- | ---
`domain` | Crawled public-provenance domain
`cui` | Normalized Romanian CUI, without the `RO` prefix
`source_url` | Sanitized page URL selected by the crawler
`final_url` | Sanitized final URL after redirects
`http_status` | Final HTTP status
`page_classes` | Controlled page classes such as contact, terms or privacy
`discovery_source` | How the page entered the crawl queue
`score` | Deterministic crawl-priority score, not an ownership probability
`text_sha256` | Hash of the locally parsed page text
`fetched_at` | Observation timestamp

The file contains evidence for 30,610 domains and 25,726 distinct active CUI
values. Consumers must still confirm the final-page CUI and resolve shared or
redirected domains before importing a domain–company association.

## Privacy and minimization

The public release excludes:

- raw HTML, page excerpts and titles;
- email addresses and telephone numbers;
- JSON-LD and extracted company-identity payloads;
- the 80.5 million bulk-discovered URLs;
- query strings, fragments and URL credentials;
- raw network and parser error messages.

Organizational contacts that pass the stricter CUI Dataset policy are published
in the separate `cui-ro-dataset` repository. This release intentionally does
not duplicate them.

## Reproducibility

The local source database reports SQLite `quick_check: ok`, zero orphan rows,
15,925,600,256 bytes and SHA-256
`3007deaa4e810bd31b871b63ba458c9305052ef7115fd4553131bf9d6b1335bd`.
The public-domain input has 435,574 rows and SHA-256
`527ee478adebc8b0da5b49f4067e0a1d04813259b0037bc3f48ede60e0abd444`.

```powershell
python scripts/export_public_http.py `
  --http-db C:\path\to\http_consolidated.sqlite3 `
  --source-summary C:\path\to\http_consolidated.summary.json `
  --public-domains data\public\domains_ro_public.csv.gz `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies `
  --output-dir C:\path\to\release

python scripts/validate_public_http_release.py `
  --release-dir C:\path\to\release `
  --public-domains data\public\domains_ro_public.csv.gz `
  --companies-dir C:\path\to\cui-ro-dataset\data\companies
```

The gzip writer fixes metadata timestamps to make repeated exports byte-for-byte
reproducible from identical inputs.
