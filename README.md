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
