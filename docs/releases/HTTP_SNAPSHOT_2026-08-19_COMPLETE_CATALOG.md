# Complete domain coverage catalog — 19 August 2026

This is the canonical release for the August snapshot. It retains every known
candidate domain, including domains unavailable during measurement and domains
that were not part of the HTTP crawl.

## Coverage

- 748,706 total `.ro` candidate domains;
- 244,076 available/complete HTTP measurements;
- 159,578 unavailable at the time of HTTP measurement;
- 1,394 robots-blocked domains;
- 3 content-decode errors;
- 343,655 domains not measured by this HTTP crawl;
- 9,567 domains documented by the Scrape The World stage-two file, including
  1,797 newly added candidates.

## Assets

- `domain_coverage_complete.csv.gz`: one row per candidate, joining provenance,
  DNS state and HTTP state;
- `http_sites_full.csv.gz`: 405,051 detailed domain-level HTTP results;
- `sitemaps_full.csv.gz`: 1,322,812 sanitized sitemap observations;
- `company_evidence_full.csv.gz`: 86,971 privacy-minimized CUI evidence rows;
- `manifest.json`: schemas, counts, source identities, SHA-256 hashes and
  classification semantics.

The release explicitly separates `unavailable_at_measurement` from
`not_measured_http`. It does not treat missing measurements as proof that a
domain does not exist or is permanently offline.

The privacy exclusions and Scrape The World attribution rules are unchanged:
raw HTML, page text, titles, email addresses, telephone numbers, JSON-LD, raw
errors and the bulk URL graph are not published. Attribution is attached only
to domains documented in a supplied source file.
