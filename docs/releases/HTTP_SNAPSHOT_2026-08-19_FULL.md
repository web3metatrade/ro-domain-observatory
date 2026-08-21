# Complete HTTP measurement — 19 August 2026

Privacy-minimized HTTP, sitemap and CUI page-evidence exports for all 405,051
domains processed by the local crawler.

## Assets

- `http_sites_full.csv.gz`: 405,051 domain-level results with actual discovery
  provenance;
- `sitemaps_full.csv.gz`: 1,322,812 unique sanitized sitemap observations;
- `company_evidence_full.csv.gz`: 86,971 page-evidence rows covering 38,689
  domains and 31,396 distinct active CUI values;
- `manifest.json`: schemas, counts, SHA-256 hashes, provenance reconciliation
  and privacy rules.

Scrape The World is attributed only to normalized domains present in the file
supplied by the company: 7,696 occur in this crawl, including 1,333 of the
137,188 domains absent from the earlier public-provenance HTTP release. Other
domains retain their actual recorded discovery sources. The HTTP and sitemap
data themselves were measured independently by this project.

The release excludes raw HTML, page text, titles, email addresses, telephone
numbers, JSON-LD, raw errors and the bulk-discovered URL graph. URL credentials,
query strings and fragments are removed. Company evidence is a candidate signal,
not a claim of domain ownership.

See the complete dataset documentation for schemas, methodology and exact
reproduction commands.
