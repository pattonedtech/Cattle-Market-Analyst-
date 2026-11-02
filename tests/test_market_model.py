"""Tests for canonical observation metadata and comparable benchmark cohorts."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from market_model import normalize_observation, build_benchmarks, summarize_observations


def row(**overrides):
    data = {
        "id": "x1", "date": "2026-10-30", "market": "Test Auction",
        "state": "California", "county": "Fresno", "region": "California",
        "category": "Feeder steer", "breed": "Not specified",
        "weight": 600, "price": 300, "price_low": 295, "price_high": 305,
        "head": 10, "price_unit": "USD/cwt", "record_type": "auction_lot_average",
        "source": "Test", "source_url": "https://example.org/report.pdf",
    }
    data.update(overrides)
    return data


class MarketModelTests(unittest.TestCase):
    def test_sale_regionality_is_not_animal_origin(self):
        item = normalize_observation(row(region="California", breed="Corriente"))
        self.assertIn("Fresno County, California", item["sale_regionality"])
        self.assertIsNone(item["animal_origin"])
        self.assertEqual(item["corriente_origin"], "unspecified")

    def test_corriente_origin_requires_explicit_source_description(self):
        native = normalize_observation(row(category="Corriente steer", breed="Native Corriente"))
        branded = normalize_observation(row(category="Corriente steer", notes="Mexican M-branded Corriente"))
        unknown = normalize_observation(row(category="Corriente steer", breed="Corriente", region="Arizona"))
        self.assertEqual(native["corriente_origin"], "Native Corriente")
        self.assertEqual(branded["corriente_origin"], "Mexican M-branded Corriente")
        self.assertEqual(unknown["corriente_origin"], "unspecified")

    def test_regional_record_retains_sale_regionality_without_fabricated_county(self):
        item = normalize_observation(row(state=None, county=None, region="Northwest Region (WA/OR/ID)"))
        self.assertIn("Northwest Region", item["sale_regionality"])
        self.assertIsNone(item["state"])
        self.assertIsNone(item["county"])

    def test_weight_class_midpoint_is_not_labeled_reported_average(self):
        item = normalize_observation(row(weight=350, weight_low=300, weight_high=399,
                                         record_type="published_weight_class_range"))
        self.assertEqual(item["weight_basis"], "published_class_midpoint")
        self.assertEqual(item["price_basis"], "published_range_midpoint")

    def test_incomplete_head_counts_use_all_observations_unweighted(self):
        items = [row(price=300, head=10), row(id="x2", price=400, head=None)]
        result = summarize_observations(items)
        self.assertEqual(result["observation_count"], 2)
        self.assertEqual(result["average"], 350)
        self.assertEqual(result["head_count"], None)
        self.assertEqual(result["average_method"], "observation_mean_head_counts_incomplete")

    def test_complete_head_counts_use_head_weighted_average(self):
        items = [row(price=300, head=10), row(id="x2", price=400, head=30)]
        result = summarize_observations(items)
        self.assertEqual(result["average"], 375)
        self.assertEqual(result["head_count"], 40)
        self.assertEqual(result["average_method"], "head_weighted_all_observations")

    def test_benchmarks_never_pool_different_units_or_source_types(self):
        items = [
            row(id="a", price=300, price_unit="USD/cwt", record_type="auction_lot_average"),
            row(id="b", price=1800, price_unit="USD/head", category="Bred cow",
                record_type="usda_replacement_weighted_average"),
            row(id="c", price=320, price_unit="USD/cwt", record_type="video_lot_contract"),
            row(id="d", price=310, price_unit="USD/cwt", record_type="published_weight_class_range"),
        ]
        result = build_benchmarks(items)
        keys = {(x["category"], x["price_unit"], x["record_type"]) for x in result["groups"]}
        self.assertEqual(len(keys), 3)
        self.assertNotIn(("Feeder steer", "USD/cwt", "published_weight_class_range"), keys)

    def test_weight_classes_remain_separate_benchmark_cohorts(self):
        result = build_benchmarks([
            row(id="a", weight=350, weight_low=300, weight_high=399),
            row(id="b", weight=450, weight_low=400, weight_high=499),
        ])
        self.assertEqual(len(result["groups"]), 2)

    def test_reported_weights_are_binned_into_100_lb_cohorts(self):
        result = build_benchmarks([
            row(id="a", weight=350, weight_low=None, weight_high=None),
            row(id="b", weight=450, weight_low=None, weight_high=None),
        ])
        bands = {(x["weight_low"], x["weight_high"]) for x in result["groups"]}
        self.assertEqual(bands, {(300, 399), (400, 499)})


if __name__ == "__main__":
    unittest.main()
