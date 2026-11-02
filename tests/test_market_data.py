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

    def test_ranchers_choice_pdf_weight_classes_are_normalized(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        html = '<a href="https://rancherschoicestockyard.com/test-report.pdf">PDF</a>'
        pdf = """Market Report
Monday, September 28, 2026
Total Head: 512
ON ON
Steers Heifers
FALSE 300-399 lbs $3.25 - $3.25 / lb 300-399 lbs $2.85 - $3.20 / lb
Feeder Bulls
FALSE 300-399 lbs $2.60 - $2.60 / lb - lbs $.00 - $.00 / lb
"""
        with patch.object(collector, "fetch", return_value=SimpleNamespace(text=html)), patch.object(
            collector, "pdf_text", return_value=pdf
        ):
            records, cow_records = collector.parse_ranchers_choice()
        self.assertEqual(len(records), 3)
        self.assertEqual(cow_records, [])
        steer = next(row for row in records if row["category"] == "Feeder steer")
        self.assertEqual(steer["weight_low"], 300)
        self.assertEqual(steer["weight_high"], 399)
        self.assertEqual(steer["price"], 325)
        self.assertEqual(steer["price_unit"], "USD/cwt")

    def test_ranchers_choice_resolves_to_san_benito_county(self):
        self.assertEqual(
            collector.geographic_fields("Ranchers Choice Stockyard", "California"),
            ("California", "San Benito"),
        )

    def test_published_weight_class_bounds_are_preserved(self):
        row = feeder_record(weight=350, weight_low=300, weight_high=399)
        accepted, rejected = collector.quality_check([row], "records")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["weight_low"], 300)

    def test_invalid_weight_class_bounds_are_rejected(self):
        row = feeder_record(weight=350, weight_low=400, weight_high=300)
        accepted, rejected = collector.quality_check([row], "records")
        self.assertEqual(accepted, [])
        self.assertIn("invalid_weight_class_bounds", rejected[0]["issues"])

    def test_regional_reports_are_not_assigned_a_county(self):
        self.assertEqual(
            collector.geographic_fields(
                "Northwest Direct Cattle Report",
                "Northwest Region (WA/OR/ID/UT)",
            ),
            (None, None),
        )

    def test_qa_report_counts_breeding_categories_separately(self):
        cow = {
            "id": "cow1", "date": "2026-10-30", "market": "Test Auction",
            "state": "California", "county": "Fresno", "category": "Cow-calf pair",
            "price": 1800, "price_unit": "USD/family", "source": "Test",
            "source_url": "https://example.org/cows.pdf", "record_type": "published_price_range",
        }
        qa = collector.build_qa_report([], [cow], [], [])
        self.assertEqual(qa["accepted_by_cow_category"]["Cow-calf pair"], 1)
        self.assertEqual(qa["accepted_by_category"], {})
        self.assertEqual(qa["accepted_by_cow_state"]["California"], 1)
        self.assertEqual(qa["cow_geographic_quality"]["cow_records_missing_state"], 0)
        self.assertEqual(qa["cow_geographic_quality"]["cow_records_missing_county"], 0)

    def test_price_outside_sanity_bounds_is_rejected(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(price=0)], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("price_outside_sanity_bounds", rejected[0]["issues"])

    def test_quality_check_adds_sale_regionality_and_origin_fields(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Corriente steer", breed="Corriente")], "records"
        )
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["corriente_origin"], "unspecified")
        self.assertIn("Kings County, California", accepted[0]["sale_regionality"])
        self.assertIsNone(accepted[0]["animal_origin"])

    def test_quality_check_respects_explicit_corriente_origin(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Corriente steer", breed="Native Corriente")], "records"
        )
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["corriente_origin"], "Native Corriente")

    def test_cow_calf_pair_must_use_per_family_unit(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Cow-calf pair", sex="Mixed", price_unit="USD/head")], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("category_unit_mismatch", rejected[0]["issues"])

    def test_bred_cow_uses_per_head_unit(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Bred cow", sex="Cow", price_unit="USD/head")], "records"
        )
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["price_unit"], "USD/head")

    def test_unclassifiable_other_category_is_quarantined(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Other", sex="Unknown")], "records"
        )
        self.assertEqual(accepted, [])
        self.assertIn("animal_category_not_classifiable", rejected[0]["issues"])

    def test_corriente_without_sex_or_origin_remains_classifiable_as_unspecified(self):
        accepted, rejected = collector.quality_check(
            [feeder_record(category="Corriente cattle (unclassified)", sex="Mixed",
                           breed="Corriente — type not specified")], "records"
        )
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["corriente_origin"], "unspecified")
        self.assertIn("Kings County, California", accepted[0]["sale_regionality"])

    def test_fetch_retries_transient_failure_and_tracks_domain_health(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        domain = "retry-test.example.org"
        collector.SOURCE_FETCH_HEALTH.pop(domain, None)
        response = SimpleNamespace(raise_for_status=lambda: None)
        with patch.object(collector.S, "get", side_effect=[RuntimeError("temporary"), response]), patch.object(collector.time, "sleep"):
            result = collector.fetch("https://retry-test.example.org/report.pdf")
        self.assertIs(result, response)
        self.assertEqual(collector.SOURCE_FETCH_HEALTH[domain]["requests"], 1)
        self.assertEqual(collector.SOURCE_FETCH_HEALTH[domain]["attempts"], 2)
        self.assertEqual(collector.SOURCE_FETCH_HEALTH[domain]["successes"], 1)
        self.assertEqual(collector.SOURCE_FETCH_HEALTH[domain]["failed_requests"], 0)
        self.assertEqual(collector.SOURCE_FETCH_HEALTH[domain]["failed_attempts"], 1)
        self.assertIn("temporary", collector.SOURCE_FETCH_HEALTH[domain]["last_error"])
        self.assertIn("report.pdf", collector.SOURCE_FETCH_HEALTH[domain]["last_failure_url"])

    def test_fetch_does_not_retry_permanent_http_404(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from requests import HTTPError

        domain = "missing-test.example.org"
        collector.SOURCE_FETCH_HEALTH.pop(domain, None)
        error = HTTPError("404 Not Found", response=SimpleNamespace(status_code=404))
        with patch.object(collector.S, "get", side_effect=error) as get, patch.object(collector.time, "sleep") as sleep:
            with self.assertRaises(HTTPError):
                collector.fetch("https://missing-test.example.org/missing.pdf")
        get.assert_called_once()
        sleep.assert_not_called()
        health = collector.SOURCE_FETCH_HEALTH[domain]
        self.assertEqual(health["failed_requests"], 1)
        self.assertIn("missing.pdf", health["failed_urls"][0])
        self.assertIn("404", health["last_error"])


if __name__ == "__main__":
    unittest.main()
