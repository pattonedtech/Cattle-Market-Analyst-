# Market data platform architecture

## Current production architecture

The live site is a static GitHub Pages dashboard. GitHub Actions runs source collectors, normalizes observations, performs QA, creates benchmark snapshots, commits versioned JSON artifacts, and deploys the dashboard.

This gives the project a shared, auditable, versioned read model. It is not a shared-write database:
- Published observations are shared and read-only to site visitors.
- Browser-entered observations are local to the browser/device and are not visible to other users.
- The collector is the only automated publisher.
- Rejected observations remain in the QA quarantine report and are excluded from published arrays.

## Canonical processing pipeline

1. **Collect:** source-specific parsers retrieve reports and record source health.
2. **Normalize:** `scripts/market_model.py` adds canonical sale geography, animal-origin, Corriente-origin, weight-basis, price-basis, and benchmark cohort fields.
3. **Validate:** `quality_check` checks identity, source URL, date, category, price, unit, weight, head count, and geography.
4. **Quarantine:** invalid or unclassifiable records are retained with original evidence and reason codes. Prior quarantine history is retained until revalidation.
5. **Benchmark:** the canonical engine creates cohorts by geography, category, unit, source series, Corriente origin, reporting period, and weight cohort.
6. **Publish:** the data and QA report are committed together and the dashboard deploys only after tests pass.

## Benchmark rules

- Auction weighted-average reports may be combined only in the explicit auction weighted-average series.
- Video contracts, direct trades, USDA state summaries, lot highlights, and published price ranges remain separate series.
- Published price ranges are shown as ranges; their midpoints are not represented as sale averages.
- Per-cwt records are separated by 100 lb cohorts when no source-published class is available.
- Cow-calf pairs are `USD/family`, bred cows/heifers are `USD/head`, and feeder cattle are `USD/cwt`. Units are not silently converted.
- Head weighting is used only when every observation in the cohort has a positive head count. Otherwise all qualifying observations are included in an observation mean.
- Corriente origin is explicit source evidence only. Sale region is never used as animal origin.
- Regional reports can have regional benchmarks, but never enter state, county, or market averages without verified geography.

## Shared-write database boundary

A production PostgreSQL schema is provided in `db/schema.sql`. It defines source, market-location, observation, quarantine, collection-run, and benchmark-snapshot tables with unit/origin constraints and lookup indexes.

**The PostgreSQL schema is not deployed.** Provisioning a database and enabling authenticated writes require a provider/project, credentials, hosting, authentication, and row-level security policies. Those resources cannot be safely inferred or created from the public GitHub Pages repository. Until the owner selects/provisions that infrastructure:
- Keep GitHub JSON as the canonical shared read model.
- Keep browser-local observations clearly labelled local-only.
- Do not place database service-role credentials in the public site.
- Do not expose anonymous database write access.

## Next migration sequence

1. Provision a managed PostgreSQL project (or approve an existing database).
2. Configure private GitHub Actions secrets for server-side credentials only.
3. Apply `db/schema.sql` and configure row-level security.
4. Build an authenticated API for manual observation submission and review.
5. Import the versioned JSON history with source IDs and original provenance.
6. Run a parallel-read comparison between JSON and PostgreSQL benchmarks.
7. Switch dashboard reads only after record counts, category/unit cohorts, geographic totals, and benchmark outputs match.
8. Keep the JSON export as a versioned backup and audit artifact.

No database migration should be considered complete until the parallel-read comparison passes.
