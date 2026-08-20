# Public HTTP snapshot — 19 August 2026

Privacy-minimized HTTP, sitemap and CUI page-evidence exports for `.ro` domains
supported by at least one redistributable provenance source.

## Assets

- `http_sites_public.csv.gz`: 267,863 domain-level crawl results;
- `sitemaps_public.csv.gz`: 909,683 unique sanitized sitemap observations;
- `company_evidence_public.csv.gz`: 70,237 page-evidence rows covering 30,610
  domains and 25,726 distinct active CUI values;
- `manifest.json`: schemas, row counts, SHA-256 hashes, source identity and
  privacy rules.

The release excludes raw HTML, page text, titles, email addresses, telephone
numbers, JSON-LD, raw errors and the bulk-discovered URL graph. URL credentials,
query strings and fragments are removed. Company evidence is a candidate signal,
not a claim of domain ownership.

All files passed a full row-by-row validator and were regenerated independently
with identical byte-level SHA-256 hashes. See
[`docs/HTTP_DATASET.md`](../HTTP_DATASET.md) for schemas, methodology and
reproduction commands.
