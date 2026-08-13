# Source inventory

This catalog separates discovery capability from redistribution permission.
Finding a domain on the public internet does not automatically grant permission
to republish the source dataset or its full metadata.

## Highest-priority coverage

1. **RoTLD zone file** — potentially authoritative for delegated names, but a
   direct agreement with RoTLD/ICI is required. ICANN CZDS obligations do not
   apply to ccTLDs.
2. **Common Crawl domain graph** — the strongest immediately accessible bulk
   source. It contains registrable domains both crawled and merely linked from
   crawled pages.
3. **Certificate Transparency** — broad coverage of names that have received a
   publicly trusted TLS certificate. Raw CT ingestion, an appropriately
   licensed bulk derivative, and incremental monitoring are distinct options.
4. **Common Crawl URL and host indexes** — add historical paths and subdomains
   after the apex-domain registry exists.
5. **Popularity/telemetry lists** — Tranco, Majestic, Cloudflare Radar, CrUX and
   Cisco Umbrella add ranking and some otherwise missed domains, but are not
   comprehensive.
6. **Active/passive DNS datasets** — OpenINTEL, Rapid7 and commercial passive
   DNS can add names and records; access and redistribution are frequently
   constrained.
7. **Structured entity sources** — the existing CUI dataset, Overture,
   OpenStreetMap, Wikidata and Romanian open-data catalogs provide smaller but
   high-value domain sets with organization context.
8. **Incremental discovery** — public web scans, archives, code search, abuse
   feeds, sitemaps and outbound links continuously add long-tail domains.

The machine-readable details and current access decisions are in
`sources.json`. Any source whose redistribution status is `unclear`,
`restricted` or `research_only` is excluded from public exports by default.

## Explicit non-sources

- Alphabetic brute force is computationally infeasible and is not a source.
- RoTLD WHOIS is a per-domain verification mechanism, not a bulk discovery
  feed; security controls must not be bypassed.
- DNS resolution only confirms the state of already-known names. It cannot
  enumerate the complete `.ro` namespace in normal operation.
- Search result pages should not be scraped where an API or bulk dataset is the
  authorized access path.

## Import phases

- Phase 1: unique registrable `.ro` domains and provenance.
- Phase 2: observed hostnames/subdomains and ranking signals.
- Phase 3: DNS records and changes over time.
- Phase 4: HTTP availability, robots and sitemap metadata.
- Phase 5: crawl legal/contact pages and generate company-link candidates.
