# Contributing market data

Thank you for helping improve the Flying P Cattle Market Analyst.

## Data submission requirements

Submit data suggestions through the **Submit a market observation** GitHub issue form, or submit a pull request if you are comfortable editing the dataset and collector. Issues are public and are not an automated write path; maintainers verify the report and decide whether to merge a change. Do not edit the published JSON directly without also providing the original evidence and explaining the normalization.

Every proposed observation must include:

- Sale date as ISO `YYYY-MM-DD`.
- Exact auction / stockyard name, physical state, and county only when verified.
- Cattle category and sex; Corriente origin must be Native, Mexican M-branded, or unspecified only when the report explicitly supports that classification.
- Average or class weight in pounds, where the source reports it.
- Price and unit (`USD/cwt`, `USD/head`, or `USD/family`).
- Number of head when the report supplies it.
- Sale channel / record type (live auction, video contract, direct trade, published range, state summary).
- Source publisher and a direct HTTPS URL to the exact report, not just the auction home page.
- A note describing any assumptions or parsing decisions.

Do not infer missing weight, head count, county, breed, Corriente origin, or sale channel. Use null / unspecified where appropriate. Regional reports must remain regional. A market's consignor town is not the physical location of the auction and must not be used to assign the market's county.

## Review process

1. Open the linked original report and verify the date, market, category, weight, price, unit, and sale type.
2. Confirm that the record is not a duplicate and that a range is not being mistaken for a weighted average.
3. Run `python -m unittest discover -s tests -v`.
4. Run `python scripts/refresh_market_data.py` in an environment with the documented dependencies.
5. Review `data/market_data_qa.json` and resolve all newly rejected records before merging.
6. Keep source URLs with records so visitors can independently verify them.

The QA gate catches structural and plausibility issues; it cannot guarantee a parser has read the correct PDF column. Human source review is required for parser changes.

## Privacy

Never submit names of individual consignors, phone numbers, addresses beyond public auction location, customer records, payment information, credentials, or other personal/confidential information. Do not submit private sale slips unless you have permission and have removed personal information. Anything merged into this public repository is public.
