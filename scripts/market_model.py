"""Canonical cattle observation normalization and benchmark calculations.

The dashboard and collectors must preserve sale geography, animal origin, units,
and source methodology as separate dimensions. This module deliberately refuses
to combine unlike record types or price units.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import re
from datetime import date, datetime, timedelta

AUCTION_AVERAGE_TYPES = {
    "auction_lot_average",
    "usda_auction",
    "usda_replacement_weighted_average",
    "third_party_auction_weighted_average",
}
BENCHMARKABLE_TYPES = AUCTION_AVERAGE_TYPES | {
    "video_lot_contract",
    "usda_direct_trade",
    "usda_video_regional",
    "usda_state_summary",
    "published_individual_price",
}


def corriente_origin(row: dict) -> str:
    """Return an explicitly supported origin; never infer origin from sale region."""
    raw = " ".join(str(row.get(k) or "") for k in (
        "corriente_origin", "corriente_classification", "breed", "description", "notes"
    ))
    normalized = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    if re.search(r"\bnative corriente\b", normalized):
        return "Native Corriente"
    if re.search(r"\bmexican m branded corriente\b|\bm branded corriente\b|\bmexican branded corriente\b", normalized):
        return "Mexican M-branded Corriente"
    category = str(row.get("category") or "").lower()
    if "corriente" in category or "corriente" in normalized:
        return "unspecified"
    return "not_applicable"


def normalize_observation(original: dict) -> dict:
    """Add canonical metadata without changing the source-reported values."""
    row = deepcopy(original)
    category = str(row.get("category") or "")
    record_type = str(row.get("record_type") or "")
    if not record_type:
        if row.get("price_low") is not None or row.get("price_high") is not None:
            record_type = "published_price_range"
        elif row.get("source_url") and row.get("source"):
            record_type = "published_individual_price"
        else:
            record_type = "manual_unclassified"
        row["record_type"] = record_type
    unit = str(row.get("price_unit") or "")
    row["corriente_origin"] = corriente_origin(row)

    # Sale geography describes the reporting location/region, never cattle origin.
    market = str(row.get("market") or "Market not identified")
    state = str(row.get("state") or "").strip()
    county = str(row.get("county") or "").strip()
    region = str(row.get("region") or "").strip()
    if county and state:
        row["sale_regionality"] = f"{market}, {county} County, {state}"
    elif state:
        row["sale_regionality"] = f"{market}, {state} (county not identified)"
    elif region:
        row["sale_regionality"] = f"{market} — {region} (state/county not assigned)"
    else:
        row["sale_regionality"] = f"{market} (sale regionality not verified)"

    # Preserve an animal-origin field only if the source supplied it.
    if not str(row.get("animal_origin") or "").strip():
        row["animal_origin"] = None

    if row.get("weight_low") is not None or row.get("weight_high") is not None or "weight_class_range" in record_type:
        row["weight_basis"] = "published_class_midpoint" if row.get("weight") is not None else "published_class_range"
    elif row.get("weight") is not None:
        row["weight_basis"] = "reported_average"
    else:
        row["weight_basis"] = "not_reported"

    if record_type in AUCTION_AVERAGE_TYPES:
        row["price_basis"] = "reported_weighted_average"
    elif record_type in {"published_weight_class_range", "published_price_range"}:
        row["price_basis"] = "published_range_midpoint"
    elif record_type == "published_lot_highlight":
        row["price_basis"] = "individual_lot_highlight"
    elif record_type == "published_individual_price":
        row["price_basis"] = "individual_sale_price"
    elif record_type in {"video_lot_contract", "usda_video_regional"}:
        row["price_basis"] = "video_contract"
    elif record_type == "usda_direct_trade":
        row["price_basis"] = "direct_trade"
    elif record_type == "usda_state_summary":
        row["price_basis"] = "regional_or_state_summary"
    else:
        row["price_basis"] = "unclassified"

    # Keep per-cwt cohorts in comparable 100 lb bands. If the source supplied
    # an actual class range, preserve it rather than replacing it with a bin.
    benchmark_low = row.get("weight_low")
    benchmark_high = row.get("weight_high")
    if benchmark_low is None and benchmark_high is None and unit == "USD/cwt" and row.get("weight") is not None:
        benchmark_low = int(float(row["weight"]) // 100) * 100
        benchmark_high = benchmark_low + 99
    row["benchmark_weight_low"] = benchmark_low
    row["benchmark_weight_high"] = benchmark_high
    row["benchmark_key"] = "|".join((
        category, unit, state or ("REGIONAL:" + str(row.get("region") or "UNVERIFIED")), record_type,
        str(benchmark_low if benchmark_low is not None else ""),
        str(benchmark_high if benchmark_high is not None else ""),
    ))
    return row


def summarize_observations(rows: list[dict]) -> dict | None:
    """Summarize one already-homogeneous cohort; weighted only if all head counts exist."""
    if not rows:
        return None
    all_head = all(
        isinstance(r.get("head"), (int, float)) and not isinstance(r.get("head"), bool)
        and r["head"] > 0 for r in rows
    )
    if all_head:
        total_head = sum(float(r["head"]) for r in rows)
        average = sum(float(r["price"]) * float(r["head"]) for r in rows) / total_head
        method = "head_weighted_all_observations"
    else:
        total_head = None
        average = sum(float(r["price"]) for r in rows) / len(rows)
        method = "observation_mean_head_counts_incomplete"
    return {
        "average": average,
        "low": min(float(r.get("price_low") if r.get("price_low") is not None else r["price"]) for r in rows),
        "high": max(float(r.get("price_high") if r.get("price_high") is not None else r["price"]) for r in rows),
        "observation_count": len(rows),
        "head_count": total_head,
        "markets": sorted({str(r.get("market")) for r in rows if r.get("market")}),
        "average_method": method,
    }


def merge_quarantine_history(current_market: list[dict], current_cows: list[dict],
                            prior: list[dict], accepted_market_ids: set,
                            accepted_cow_ids: set) -> tuple[list[dict], list[dict]]:
    """Keep prior quarantine rows until accepted or replaced by a fresh rejection."""
    market = list(current_market)
    cows = list(current_cows)
    current_keys = {
        (row.get("collection"), row.get("id")) for row in market + cows
    }
    for row in prior:
        collection, rid = row.get("collection"), row.get("id")
        key = (collection, rid)
        if key in current_keys:
            continue
        if collection == "records" and rid not in accepted_market_ids:
            market.append(row)
        elif collection == "cow_records" and rid not in accepted_cow_ids:
            cows.append(row)
    return market, cows


WESTERN_STATES = {
    "Washington", "Oregon", "California", "Nevada", "Idaho",
    "Montana", "Wyoming", "Utah", "Arizona", "New Mexico",
}


def build_benchmarks(records: list[dict], cow_records: list[dict] | None = None,
                     as_of: date | None = None) -> dict:
    """Build published benchmark cohorts for the dashboard.

    Cohorts are separated by geography, category, exact unit, source series,
    Corriente origin, period, and weight class. The composite auction series
    includes auction weighted-average record types only; video/direct/range
    series are never included in it.
    """
    as_of = as_of or date.today()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    group_sources: dict[tuple, set] = defaultdict(set)
    periods = (
        ("all", None),
        ("365", as_of - timedelta(days=365)),
        ("90", as_of - timedelta(days=90)),
    )
    all_rows = list(records) + list(cow_records or [])
    for original in all_rows:
        row = normalize_observation(original)
        record_type = str(row.get("record_type") or "")
        if record_type not in BENCHMARKABLE_TYPES:
            continue
        if not row.get("category") or not row.get("price_unit"):
            continue
        if not row.get("state") and not row.get("region"):
            continue
        try:
            sale_date = datetime.strptime(str(row.get("date")), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        origin = row.get("corriente_origin") or "not_applicable"
        source_series = [record_type]
        if record_type in AUCTION_AVERAGE_TYPES:
            source_series.append("auction_weighted_average")
        weight_cohorts = [("all", None, None)]
        if row["price_unit"] == "USD/cwt" and row.get("benchmark_weight_low") is not None:
            lo = int(row["benchmark_weight_low"])
            hi = int(row["benchmark_weight_high"])
            weight_cohorts.append((f"{lo}-{hi}", lo, hi))
        geographies = [(row["state"] or "Regional / unassigned", row.get("region") if not row.get("state") else None)]
        if row.get("state") in WESTERN_STATES:
            geographies.append(("Western", None))
        for period_days, cutoff in periods:
            if cutoff is not None and sale_date < cutoff:
                continue
            for series in source_series:
                for weight_cohort, weight_low, weight_high in weight_cohorts:
                    for geography, region_scope in geographies:
                        key = (
                            geography, region_scope, row["category"], row["price_unit"], series,
                            origin, period_days, weight_cohort, weight_low, weight_high,
                        )
                        groups[key].append(row)
                        group_sources[key].add(record_type)
    output = []
    for key in sorted(groups, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        state, region_scope, category, unit, series, origin, period_days, weight_cohort, weight_low, weight_high = key
        output.append({
            "state": state,
            "region": region_scope,
            "category": category,
            "price_unit": unit,
            "record_type": series,
            "corriente_origin": origin,
            "period_days": period_days,
            "weight_cohort": weight_cohort,
            "weight_low": weight_low,
            "weight_high": weight_high,
            "source_record_types": sorted(group_sources[key]),
            **summarize_observations(groups[key]),
        })
    return {
        "schema_version": 2,
        "as_of": as_of.isoformat(),
        "methodology": "Benchmarks are grouped by state/Western, category, exact price unit, source series, Corriente origin, reporting period, and weight cohort. Auction weighted-average types may be combined only in the explicit auction_weighted_average series. Video, direct trade, ranges, and state summaries remain separate. Head weighting is used only when every observation has a positive head count; otherwise all observations contribute equally.",
        "groups": output,
    }
