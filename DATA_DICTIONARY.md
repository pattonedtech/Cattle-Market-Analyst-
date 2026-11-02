# Market observation data dictionary

The public collector output is `data/market_observations.json`. A record is one reported market observation, not necessarily one individual animal.

## Core fields

| Field | Meaning |
|---|---|
| `id` | Stable observation identifier used for deduplication. |
| `market_id` | Stable identifier derived from the canonical market label. |
| `date` | Sale/report date in ISO `YYYY-MM-DD`. |
| `market` | Auction yard or reporting market. Regional reports use a regional report name. |
| `state`, `county` | Physical location of the reporting auction only when established. County may be null. |
| `geography_method` | How the location was assigned; regional reports are explicitly left unassigned. |
| `category`, `sex`, `breed` | Animal class and source-described type. Unknown breed/type is not inferred. |
| `weight` | Reported average weight or representative class midpoint in pounds. When a class range is published, see `weight_low` / `weight_high`; the midpoint is only a filter/chart anchor, not an observed average. |
| `weight_low`, `weight_high` | Original published weight-class bounds in pounds, when supplied. |
| `price`, `price_low`, `price_high` | Reported price or midpoint and its published range. |
| `price_unit` | `USD/cwt`, `USD/head`, or `USD/family`; never compare unlike units. |
| `head` | Reported head count, or null if unavailable. |
| `record_type` | Sale/report method, such as auction average, published range, video contract, direct trade, or state summary. |
| `source`, `source_url` | Publisher and direct URL to the original report. |
| `qa_status`, `qa_warnings` | Collector validation outcome and non-fatal review flags. |

## Interpretation rules

- Auction geography means the physical reporting yard, not the consignor's town, cattle origin, or destination.
- County attribution is null unless the auction's physical location is mapped. A multi-state/regional report must not be represented as a yard observation.
- Weights are pounds. Price units must be interpreted literally. For published weight-class ranges, `weight_low` and `weight_high` preserve the actual class; `weight` is the class midpoint used for simple filtering only, not a measured average.
- A published range is not a weighted average. A video contract is not a live auction result. A direct-trade report is not an auction-yard price.
- Corriente origin is a separate classification dimension. Native, Mexican M-branded, and unspecified must not be conflated. "Fresh Corriente" is a sale description, not an origin.
- Null means not reported / not established, not zero.
- The dataset contains public market observations only. Do not add personal consignor or buyer details.
