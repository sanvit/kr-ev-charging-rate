import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.parse_rates import generate_json


class ParseRatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output = self.directory / "charging_rates.json"
        self.output.write_text('{"previous":"data"}', encoding="utf-8")
        for index, stage in enumerate(("s1", "s2", "s3", "s4", "s5")):
            self.write_band(stage, 290 + index, 390 + index)

    def write_band(self, stage, fee_a, fee_b):
        pd.DataFrame(
            [["카드 A", fee_a, "-"], ["카드 B", None, fee_b]],
            columns=["발급/이용", "충전소 A", "충전소 B"],
        ).to_excel(self.directory / f"{stage}.xlsx", index=False)

    def assert_rejected(self, message):
        previous = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, message):
            generate_json(self.directory, self.output)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_merges_all_bands_without_transposing_issuers_and_providers(self):
        generate_json(self.directory, self.output)
        rates = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(set(rates), {"카드 A", "카드 B"})
        self.assertEqual(set(rates["카드 A"]), {"충전소 A"})
        self.assertEqual(set(rates["카드 B"]), {"충전소 B"})
        self.assertEqual(rates["카드 A"]["충전소 A"], [
            {"min_kW": 0, "max_kW": 29, "price": 290.0},
            {"min_kW": 30, "max_kW": 49, "price": 291.0},
            {"min_kW": 50, "max_kW": 99, "price": 292.0},
            {"min_kW": 100, "max_kW": 199, "price": 293.0},
            {"min_kW": 200, "max_kW": 9999, "price": 294.0},
        ])
        self.assertEqual(rates["카드 B"]["충전소 B"][0]["price"], 390.0)
        first_output = self.output.read_bytes()
        generate_json(self.directory, self.output)
        self.assertEqual(self.output.read_bytes(), first_output)

    def test_handles_decimals_thousands_separators_and_unavailable_zero(self):
        self.write_band("s1", "1,234.5", 0)
        self.write_band("s2", " 307.2 ", 250)
        rates = generate_json(self.directory, self.output)
        self.assertEqual(rates["카드 A"]["충전소 A"][0]["price"], 1234.5)
        self.assertEqual(rates["카드 A"]["충전소 A"][1]["price"], 307.2)
        self.assertEqual(rates["카드 B"]["충전소 B"][0]["min_kW"], 30)

    def test_rejects_html_error_page_and_preserves_previous_json(self):
        (self.directory / "s1.xlsx").write_text("<html>404 Not Found</html>")
        self.assert_rejected("expected an XLSX workbook")

    def test_requires_every_band_even_if_earlier_bands_were_valid(self):
        (self.directory / "s5.xlsx").unlink()
        self.assert_rejected("Missing power-band workbook")

    def test_rejects_band_with_no_rates(self):
        self.write_band("s5", "-", None)
        self.assert_rejected("no charging rates found")

    def test_rejects_unrecognized_header(self):
        pd.DataFrame([["card", 300]], columns=["changed", "provider"]).to_excel(
            self.directory / "s1.xlsx", index=False
        )
        self.assert_rejected("unexpected roaming fee matrix layout")

    def test_rejects_filtered_or_inconsistent_business_lists(self):
        pd.DataFrame([["카드 A", 300]], columns=["발급/이용", "충전소 A"]).to_excel(
            self.directory / "s5.xlsx", index=False
        )
        self.assert_rejected("businesses differ")

    def test_rejects_invalid_rates(self):
        for value in ("unknown", -1, "inf"):
            with self.subTest(value=value):
                self.write_band("s5", value, 300)
                self.assert_rejected("invalid fee")

    def test_accepts_different_row_and_column_order_between_bands(self):
        pd.DataFrame(
            [["카드 B", 450, "-"], ["카드 A", "-", 350]],
            columns=["발급/이용", "충전소 B", "충전소 A"],
        ).to_excel(self.directory / "s5.xlsx", index=False)
        rates = generate_json(self.directory, self.output)
        self.assertEqual(rates["카드 A"]["충전소 A"][-1]["price"], 350.0)
        self.assertEqual(rates["카드 B"]["충전소 B"][-1]["price"], 450.0)


if __name__ == "__main__":
    unittest.main()
