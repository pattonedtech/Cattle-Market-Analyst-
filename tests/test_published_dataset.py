"""Repository-wide integrity tests for the published public market archive."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import refresh_market_data as collector


class PublishedDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "data" / "market_observations.json"
        with path.open(encoding="utf-8") as handle:
            cls.payload = json.load(handle)
        qa_path = ROOT / "data" / "market_data_qa.json"
        with qa_path.open(encoding="utf-8") as handle:
            cls.qa = json.load(handle)

    def check_collection(self, rows, require_record_type):
        ids = set()
        allowed_units = {"USD/cwt", "USD/head", "USD/family"}
        today = datetime.now(timezone.utc).date()
        for row in rows:
            self.assertTrue(row.get("id"), "record missing id")
            self.assertNotIn(row["id"], ids, f"duplicate id: {row['id']}")
            ids.add(row["id"])
            self.assertTrue(row.get("market"), row)
            self.assertTrue(row.get("category"), row)
            self.assertTrue(row.get("source"), row)
            self.assertTrue(str(row.get("source_url", "")).startswith("https://"), row)
            self.assertIn(row.get("price_unit"), allowed_units, row)
            self.assertGreater(float(row.get("price", 0)), 0, row)
            date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            self.assertLessEqual(date, today, row)
            if require_record_type:
                self.assertTrue(row.get("record_type"), row)
            if row.get("county"):
                self.assertTrue(row.get("state"), row)
            inferred_state, inferred_county = collector.geographic_fields(
                row.get("market"), row.get("region") or row.get("state")
            )
            if inferred_state and row.get("state"):
                self.assertEqual(row["state"], inferred_state, row)
            if inferred_county and row.get("county"):
                self.assertEqual(row["county"], inferred_county, row)
            if row.get("price_low") is not None or row.get("price_high") is not None:
                low = float(row.get("price_low", row["price"]))
                high = float(row.get("price_high", row["price"]))
                self.assertLessEqual(low, float(row["price"]) + 0.02, row)
                self.assertGreaterEqual(high + 0.02, float(row["price"]), row)
            if row.get("weight") is not None:
                self.assertGreaterEqual(float(row["weight"]), 50, row)
                self.assertLessEqual(float(row["weight"]), 3000, row)
            if row.get("weight_low") is not None or row.get("weight_high") is not None:
                low_w, high_w = float(row["weight_low"]), float(row["weight_high"])
                self.assertLessEqual(50, low_w, row)
                self.assertLessEqual(low_w, high_w, row)
                self.assertLessEqual(high_w, 3000, row)
                self.assertTrue(low_w <= float(row["weight"]) <= high_w, row)

    def test_market_records_are_source_linked_and_unique(self):
        self.check_collection(self.payload.get("records", []), True)

    def test_cow_market_records_are_source_linked_and_unique(self):
        self.check_collection(self.payload.get("cow_records", []), False)

    def test_qa_report_matches_published_arrays(self):
        self.assertEqual(self.qa["market_observations"]["accepted"], len(self.payload.get("records", [])))
        self.assertEqual(self.qa["cow_market_observations"]["accepted"], len(self.payload.get("cow_records", [])))
        self.assertTrue(all(row.get("source_url", "").startswith("https://") for row in self.qa["rejected_observations"]))

    def test_qa_report_has_geographic_and_source_coverage(self):
        self.assertGreaterEqual(self.qa.get("schema_version", 0), 2)
        self.assertIn("geographic_quality", self.qa)
        self.assertIn("accepted_by_source_domain", self.qa)
        self.assertTrue(
            any(source.get("name") == "Ranchers Choice Stockyard"
                for source in self.qa.get("source_registry", []))
        )

    def test_published_counts_match_arrays(self):
        self.assertEqual(
            self.payload.get("record_count"), len(self.payload.get("records", []))
        )
        self.assertEqual(
            self.payload.get("cow_record_count"), len(self.payload.get("cow_records", []))
        )


if __name__ == "__main__":
    unittest.main()
