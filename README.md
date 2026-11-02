# Flying P Cattle Market Analyst

A lightweight, mobile-friendly ranch dashboard for local cattle-market observations and ranch buying decisions.

## Current features

- Market observation log for Visalia Livestock Market, Overland Stock Yard, and other markets
- Filters by market and cattle category
- CSV import/export for sale observations
- Beef finishing bid calculator using ranch-entered costs and target margin
- Replacement-heifer ownership-cost worksheet
- 12-month ranch planning calendar
- Browser-local storage for market observations

## Run locally

Open `index.html` in a modern browser. No build tools or external JavaScript dependencies are required.

## Publish with GitHub Pages

The repository includes a GitHub Actions workflow at `.github/workflows/deploy-pages.yml`. In the repository settings, set **Pages → Build and deployment → Source** to **GitHub Actions** if it is not already selected. Each push to `main` will then publish the site.

## Data and limitations

The dashboard starts with no preloaded cattle prices. Enter or import observations to build a local history. Keep markets, breed/type, sex, weight class, and sale type distinct when comparing prices. Corriente, Angus-cross, and dairy cattle should not be blended into one benchmark.

Market observations are saved in the current browser on the current device. Use CSV export to back up and move data between devices. The dashboard does not yet automatically retrieve sale reports or generate statistically validated forecasts.

The beef calculator currently assumes the entered feed period begins at 600 lb. If cattle are purchased below 600 lb, include backgrounding expenses in other costs until a dedicated backgrounding model is added. Processing is excluded under the assumption that the customer pays the processor separately.

This is a decision-support tool, not a guarantee of market prices or investment returns.


## Automatic market history refresh

A GitHub Actions workflow runs the public-source collector daily (and can be run manually from Actions). It checks Overland Stock Yard's public market-report archive and the current Visalia Livestock Market snapshot PDFs, preserving source URLs and record type. The dashboard loads the published JSON dataset automatically when opened. Overland's archive contains many years of video-auction reports, but much of that series is Holstein/dairy-cross cattle; it is not a substitute for Angus/Angus-cross sale history. Visalia's public snapshot page currently exposes only a small recent set of PDFs. The collector does not manufacture missing history, and the historical analysis displays sample counts rather than presenting weak samples as dependable seasonal patterns.


## Expanded regional market data and investment models

The collector now includes the USDA AMS Roswell (1782), Clovis (1781), Cattlemen's/Belen (1783), Santa Teresa (1290), New Mexico Weekly Summary (1784), and Southwest Direct Cattle AZ/CA/NV (2940) report families, plus the Marana Stockyards archive (historical weekly reports back to 2022), Visalia snapshots, and Overland video lots. A source register also lists other California, Arizona, Nevada, and New Mexico sale barns; a listed source is not represented as automatically parsed until its parser is enabled. Marana bred-cow and cow-calf-pair sale prices are stored separately as USD/head, not mixed with feeder $/cwt records.

The Investment Outlook supports feeder cattle bought at 400–600 lb, a backgrounding period to 600 lb, finishing days/costs, live-auction or direct-beef sale channels, target net margin, local basis inputs, and a cow ownership cash-flow/NPV worksheet. Deferred feeder and live cattle futures are shown by contract month. Futures are exchange benchmarks, not guaranteed cash sale prices; local basis, grade, weight, shrink, health, and sale channel matter. The cow worksheet's payback estimate is simple cash-flow payback; NPV is separately discounted. No automatic buy/sell recommendation should be treated as validated until sufficient comparable history and basis observations exist.

## Public data, provenance, and quality assurance

Current source coverage includes official auction reports from Visalia, Overland, Marana, Willcox, Fallon, Burley, and Ranchers Choice Stockyard, plus USDA and Cattle Channel regional series. Ranchers Choice reports preserve weight-class ranges, lot highlights, slaughter cow/bull ranges, and per-head/per-family breeding values as separate observation types. Dos Palos Y and additional California yards remain listed as source leads until their PDF parsers pass QA.

See [DATA_DICTIONARY.md](DATA_DICTIONARY.md) for field definitions and comparison rules.

The current published JSON is a versioned, read-only shared data store. Manual observations entered in a browser remain local to that browser; the static GitHub Pages site cannot safely accept shared writes without an authenticated backend. The collector now publishes a canonical benchmark engine output grouped by state, category, exact price unit, record type, and weight class. It keeps sale regionality separate from animal origin, and only classifies Corriente as Native or Mexican M-branded when the source explicitly says so. When head counts are incomplete, all qualifying observations contribute to an unweighted observation mean rather than silently dropping records.

The public Pages site is a read-only presentation of the repository's published dataset. It is not a shared multi-user database. Observations manually entered in a browser are stored in that browser's local storage and are not automatically shared with other visitors or written to GitHub. Export a CSV to back up local entries. Do not enter personal, confidential, customer, or payment information.

The scheduled collector retrieves public auction and USDA market reports. Each published observation should retain its sale date, market name, state, county when independently established, category, weight, price, price unit, record type, source name, and direct source URL. Regional or multi-state reports remain regional; they are not assigned to a yard or county. A source listed as "parser pending" is a discovery lead, not an automated data source.

### Quality controls

- The collector validates dates, required fields, HTTPS source links, state and county attribution, market identity, category, weight, head count, price, price range, and unit.
- Public-source fetches receive one bounded retry; final failures are recorded by source domain in the QA report. Prior quarantined observations remain visible until they are revalidated or superseded by a fresh rejection.
- Invalid records are excluded from the published observation arrays and recorded in `data/market_data_qa.json` with source URL and machine-readable rejection reasons.
- Records that cannot be assigned a meaningful cattle category are quarantined; Corriente can remain in an unclassified Corriente category when origin or sex is not identified, with sale regionality preserved.
- Unusual prices are warnings for review, not automatic proof of an error.
- The refresh workflow runs unit tests before publication, quarantines invalid observations, emits a warning when at least 5% of newly collected market records fail hard validation, and blocks publication when at least 20% fail. These thresholds are circuit breakers, not permission to accept invalid rows.
- Source report types (live auction averages, published ranges, video contracts, direct trades, and state summaries) remain distinct. Do not pool unlike sale channels or units into a single benchmark.
- The QA report is a screening process, not a guarantee that every source parser interpreted every PDF correctly. Human review of the linked original report remains necessary.

### Multi-user and security model

- Visitors can read and download the public site and data. Browser-local entries and preferences remain local to that browser unless a user deliberately exports and shares them.
- Public visitors do not receive repository write credentials. Never put GitHub tokens, API keys, passwords, private customer records, or other secrets in HTML, JavaScript, JSON, issues, or pull requests.
- Shared dataset changes must be submitted as a GitHub pull request with direct report URLs and a description of the parsing or correction. Maintainers review the source and QA results before merging.
- Keep `main` protected: require pull requests and at least one maintainer review, require the `quality` check from **Market data pull request quality checks**, block force pushes and branch deletion, and restrict direct pushes to trusted maintainers. Enable CODEOWNERS review for `/data/`, `/scripts/`, `/tests/`, and `/.github/workflows/`. These repository settings must be enabled by a repository administrator; documentation alone does not enforce them.
- The market refresh workflow is the only automated writer of generated market JSON. Its GitHub Actions token is scoped to this repository and its write permission is needed to publish scheduled updates. Do not add public form submissions that write directly to the repository.
- Treat all imported CSVs and external report text as untrusted input. Never execute uploaded files or remote scripts.

### Contributing market observations

See [CONTRIBUTING.md](CONTRIBUTING.md) for the required source evidence and review process. See [SECURITY.md](SECURITY.md) for vulnerability reporting. The source register and live QA summary are displayed in the dashboard; open the linked original report to verify any value before relying on it for a transaction.


See [DATA_PLATFORM_ARCHITECTURE.md](docs/DATA_PLATFORM_ARCHITECTURE.md) for the production architecture and [schema.sql](db/schema.sql) for the PostgreSQL migration target. The database schema is not deployed; provisioning and credentials are required before shared writes can be enabled.
