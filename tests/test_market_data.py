"""Unit tests for publication-gate validation of public market observations."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_market_data as collector


def feeder_record(**overrides):
    row = {
        "id": "test-feeder-001",
        "date": "2026-10-29",
        "market": "Overland Stock Yard",
        "market_id": "overland-test",
        "region": "California",
        "state": "California",
        "county": "Kings",
        "geography_method": "verified_market_registry",
        "category": "Feeder steer",
        "sex": "Steer",
        "breed": "Not specified",
        "weight": 600,
        "price": 300,
        "price_low": 300,
        "price_high": 300,
        "head": 5,
        "price_unit": "USD/cwt",
        "record_type": "auction_lot_average",
        "source": "Test source",
        "source_url": "https://example.org/report.pdf",
    }
    row.update(overrides)
    return row


class MarketDataQualityTests(unittest.TestCase):
    def test_valid_source_linked_feeder_record_passes(self):
        accepted, rejected = collector.quality_check([feeder_record()], "records")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["qa_status"], "passed")

    def test_missing_source_url_is_rejected(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(source_url="")], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("missing_or_invalid_https_source_url", rejected[0]["issues"])

    def test_market_county_conflict_is_rejected(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(county="Orange")], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn(
            "market_county_conflicts_with_location_registry",
            rejected[0]["issues"],
        )

    def test_cwt_record_without_weight_is_rejected(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(weight=None)], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("missing_weight_for_cwt_record", rejected[0]["issues"])

    def test_head_price_record_can_omit_weight(self):
        row = feeder_record(
            category="Bred cow",
            weight=None,
            price=1850,
            price_low=1700,
            price_high=2000,
            price_unit="USD/head",
            record_type=None,
        )
        accepted, rejected = collector.quality_check([row], "cow_records")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_burley_market_resolves_to_cassia_county(self):
        self.assertEqual(
            collector.geographic_fields("Burley Livestock Auction", "Idaho"),
            ("Idaho", "Cassia"),
        )

    def test_regional_reports_are_not_assigned_a_county(self):
        self.assertEqual(
            collector.geographic_fields(
                "Northwest Direct Cattle Report",
                "Northwest Region (WA/OR/ID/UT)",
            ),
            (None, None),
        )

    def test_price_outside_sanity_bounds_is_rejected(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(price=0)], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("price_outside_sanity_bounds", rejected[0]["issues"])


if __name__ == "__main__":
    unittest.main()
