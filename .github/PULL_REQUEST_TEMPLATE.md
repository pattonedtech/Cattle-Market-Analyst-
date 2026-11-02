## Market-data change

- [ ] This change includes the exact original report URL(s).
- [ ] Sale date, market, state, county (if verified), category, weight, price, unit, and sale type were checked against the report.
- [ ] Regional reports have not been assigned to an auction yard or county.
- [ ] Missing information remains null / unspecified; nothing has been inferred to fill gaps.
- [ ] Duplicate observations and price-unit conversions were checked.
- [ ] Personal / consignor information and credentials are not included.
- [ ] `python -m unittest discover -s tests -v` passes.

## Sources and interpretation

List the direct HTTPS report links and explain any parsing, classification, or conversion decisions.

## QA results

Summarize the accepted/rejected counts and any warnings from `data/market_data_qa.json`.
