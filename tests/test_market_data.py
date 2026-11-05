"""Unit tests for publication-gate validation of public market observations."""
import re
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

    def test_overland_sale_date_uses_california_local_date_at_utc_rollover(self):
        from datetime import datetime, timezone

        row = feeder_record(date="2026-11-04")
        before_local_midnight = datetime(2026, 11, 4, 2, 22, tzinfo=timezone.utc)
        accepted, rejected = collector.quality_check(
            [row], "records", as_of_utc=before_local_midnight
        )
        self.assertEqual(accepted, [])
        self.assertIn("sale_date_in_future", rejected[0]["issues"])
        self.assertEqual(rejected[0]["market_timezone"], "America/Los_Angeles")

        at_local_date = datetime(2026, 11, 4, 8, 1, tzinfo=timezone.utc)
        accepted, rejected = collector.quality_check(
            [row], "records", as_of_utc=at_local_date
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["market_timezone"], "America/Los_Angeles")

    def test_idaho_markets_resolve_to_their_actual_timezones(self):
        self.assertEqual(
            collector.market_timezone("Burley Livestock Auction", "Idaho"),
            "America/Boise",
        )
        self.assertEqual(
            collector.market_timezone("Lewiston Livestock Auction", "Idaho"),
            "America/Los_Angeles",
        )

    def test_unknown_market_timezone_quarantines_ambiguous_current_utc_date(self):
        from datetime import datetime, timezone

        row = feeder_record(
            market="Unverified Idaho Auction",
            state="Idaho",
            region="Idaho",
            date="2026-11-04",
        )
        accepted, rejected = collector.quality_check(
            [row], "records",
            as_of_utc=datetime(2026, 11, 4, 2, 22, tzinfo=timezone.utc),
        )
        self.assertEqual(accepted, [])
        self.assertIn("market_timezone_unverified", rejected[0]["issues"])

    def test_publication_gate_rechecks_dates_and_quarantines_future_records(self):
        from datetime import datetime, timezone

        rows = [feeder_record(date="2026-11-04")]
        accepted, rejected = collector.publication_date_gate(
            rows, "records",
            as_of_utc=datetime(2026, 11, 4, 2, 22, tzinfo=timezone.utc),
        )
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("sale_date_in_future", rejected[0]["issues"])

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

    def test_historical_geography_backfill_preserves_verified_market_registry(self):
        row = {"market": "Overland Stock Yard", "region": "California"}
        result = collector.backfill_market_geography(row)
        self.assertEqual(result["state"], "California")
        self.assertEqual(result["county"], "Kings")
        self.assertEqual(result["geography_method"], "verified_market_registry")
        self.assertTrue(result.get("market_id"))

    def test_historical_geography_backfill_preserves_source_state_without_guessing_county(self):
        row = {"market": "Unknown Auction", "region": "Arizona", "state": "Arizona"}
        result = collector.backfill_market_geography(row)
        self.assertEqual(result["state"], "Arizona")
        self.assertIsNone(result.get("county"))
        self.assertEqual(result["geography_method"], "source_report_state_only")
        self.assertIsNone(result.get("market_id"))

    def test_historical_geography_backfill_keeps_multistate_reports_unassigned(self):
        row = {"market": "Northwest Direct Cattle Report", "region": "Northwest Region (WA/OR/ID)"}
        result = collector.backfill_market_geography(row)
        self.assertIsNone(result.get("state"))
        self.assertIsNone(result.get("county"))
        self.assertEqual(result["geography_method"], "not_verified")
        self.assertIsNone(result.get("market_id"))

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

    def test_unclassifiable_breeding_stock_is_quarantined(self):
        cow = feeder_record(category="Other", sex="Unknown", price_unit=None)
        accepted, rejected = collector.quality_check([cow], "cow_records")
        self.assertEqual(accepted, [])
        self.assertIn("animal_category_not_classifiable", rejected[0]["issues"])

    def test_willcox_cow_category_comes_from_description_not_row_order(self):
        self.assertEqual(collector.classify_willcox_cow_lot("BLACK BALDY PAIR"),
                         ("Cow-calf pair", "Mixed", "USD/family"))
        self.assertEqual(collector.classify_willcox_cow_lot("BRED COW"),
                         ("Bred cow", "Cow", "USD/head"))
        self.assertEqual(collector.classify_willcox_cow_lot("BLACK BALDY"),
                         ("Other", "Unknown", None))

    def test_willcox_feeder_category_comes_from_description(self):
        self.assertEqual(collector.classify_willcox_feeder_lot("ANGUS STR"),
                         ("Feeder steer", "Steer"))
        self.assertEqual(collector.classify_willcox_feeder_lot("ANGUS HFR"),
                         ("Feeder heifer", "Heifer"))
        self.assertEqual(collector.classify_willcox_feeder_lot("BLACK BALDY"),
                         ("Other", "Unknown"))

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


    def test_cattle_channel_discovery_follows_nested_sitemaps_and_filters_hosts(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        sitemap = """<?xml version="1.0"?>
        <sitemapindex>
          <sitemap><loc>https://cattlechannel.com/reports-sitemap.xml</loc></sitemap>
        </sitemapindex>"""
        child = """<?xml version="1.0"?>
        <urlset>
          <url><loc>https://cattlechannel.com/market-report/idaho/burley-livestock-auction/2026-10-08</loc></url>
          <url><loc>https://cattlechannel.com/market-report/oregon/northwest-livestock-commission/2026-10-06</loc></url>
          <url><loc>https://untrusted.example/market-report/idaho/fake-market/2026-10-08</loc></url>
        </urlset>"""
        index = """<html><body>
          <a href="/market-report/oregon/producers-vale/2026-09-09">Report</a>
        </body></html>"""
        responses = {
            "https://cattlechannel.com/sitemap.xml": sitemap,
            "https://cattlechannel.com/reports-sitemap.xml": child,
            "https://cattlechannel.com/market-report/": index,
        }
        with patch.object(
            collector, "fetch",
            side_effect=lambda url: SimpleNamespace(text=responses[url]),
        ) as mocked_fetch:
            found = collector.discover_cattle_channel_report_urls()

        self.assertIn(
            "https://cattlechannel.com/market-report/idaho/burley-livestock-auction/2026-10-08",
            found,
        )
        self.assertIn(
            "https://cattlechannel.com/market-report/oregon/northwest-livestock-commission/2026-10-06",
            found,
        )
        self.assertIn(
            "https://cattlechannel.com/market-report/oregon/producers-vale/2026-09-09",
            found,
        )
        self.assertFalse(any("untrusted.example" in url for url in found))
        self.assertEqual(
            [call.args[0] for call in mocked_fetch.call_args_list],
            [
                "https://cattlechannel.com/sitemap.xml",
                "https://cattlechannel.com/reports-sitemap.xml",
                "https://cattlechannel.com/market-report/",
            ],
        )

    def test_cattle_channel_discovery_continues_when_sitemap_fails(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        index = """<html><body>
          <a href="/market-report/idaho/burley-livestock-auction/2026-10-08">Report</a>
        </body></html>"""
        def fake_fetch(url):
            if url.endswith("sitemap.xml"):
                raise RuntimeError("temporary sitemap failure")
            return SimpleNamespace(text=index)

        with patch.object(collector, "fetch", side_effect=fake_fetch):
            found = collector.discover_cattle_channel_report_urls()

        self.assertEqual(
            found,
            {"https://cattlechannel.com/market-report/idaho/burley-livestock-auction/2026-10-08"},
        )


    def test_cattle_channel_report_urls_are_canonical_and_date_validated(self):
        pattern = re.compile(
            r"/market-report/(?:idaho|oregon)/[^/]+/20\d{2}-\d{2}-\d{2}/?",
            re.I,
        )
        normalize = collector.normalize_cattle_channel_report_url
        self.assertEqual(
            normalize(
                "https://www.cattlechannel.com/market-report/idaho/burley/2026-10-08/?utm_source=test#top",
                "https://cattlechannel.com/sitemap.xml",
                pattern,
            ),
            "https://cattlechannel.com/market-report/idaho/burley/2026-10-08",
        )
        for unsafe in (
            "http://cattlechannel.com/market-report/idaho/burley/2026-10-08",
            "https://cattlechannel.com.evil.example/market-report/idaho/burley/2026-10-08",
            "https://cattlechannel.com:8443/market-report/idaho/burley/2026-10-08",
            "https://cattlechannel.com:bad/market-report/idaho/burley/2026-10-08",
            "https://user@cattlechannel.com/market-report/idaho/burley/2026-10-08",
            "https://cattlechannel.com/market-report/idaho/burley/2026-02-30",
            "https://cattlechannel.com/market-report/idaho/burley/not-a-date",
        ):
            with self.subTest(url=unsafe):
                self.assertIsNone(
                    normalize(unsafe, "https://cattlechannel.com/sitemap.xml", pattern)
                )


if __name__ == "__main__":
    unittest.main()
