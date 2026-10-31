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
