import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from scripts import parse_station_fees
from scripts.parse_station_fees import HEADER, parse_station_fees as parse


ME = [295, 307.2, 325.6, 348.4, 393.1]


class ParseStationFeesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.rates = self.directory / "operator_rates.json"
        self.rates.write_text(json.dumps({"operators": {
            "기후에너지환경부": {"member": ME, "nonMember": ME},
            "채비": {"member": [289, 315, 410, 430, 430], "nonMember": [400] * 5},
        }}, ensure_ascii=False), encoding="utf-8")
        self.xlsx = self.directory / "stations.xlsx"
        patcher = mock.patch.object(parse_station_fees, "MIN_STATIONS", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rows, header=HEADER):
        pd.DataFrame(rows, columns=header).to_excel(self.xlsx, index=False)

    def row(self, number, name, address, operator, outputs, prices, roaming="있음"):
        return [number, name, address, operator, outputs, *prices, roaming]

    def test_links_stations_to_operators_and_merges_split_rows(self):
        self.write([
            self.row(1, "가야시장 주차장", "경상남도 합천군 가야면 황산3길 11-2", "기후에너지환경부", "100", ME),
            self.row(2, "가야시장 주차장", "경상남도 합천군 가야면 황산3길 11-2", "기후에너지환경부", "7 · 300", ME),
            self.row(3, "성수 충전소", "서울특별시 성동구 성수동 1", "채비", "200", [289, 315, 410, 430, 430], "-"),
            self.row(4, "시청 주차장", "전북특별자치도 익산시 1", "익산시", "50", ["-"] * 5),
        ])
        data = parse(self.xlsx, self.rates)
        self.assertEqual(data["fields"], ["name", "address", "operator", "outputs", "roaming", "prices"])
        self.assertEqual(data["count"], 3)
        rows = {row[0]: row for row in data["stations"]}
        self.assertEqual(data["operators"][rows["가야시장 주차장"][2]], "기후에너지환경부")
        self.assertEqual(rows["가야시장 주차장"][3], [7, 100, 300])
        self.assertEqual(rows["가야시장 주차장"][4], 1)
        self.assertEqual(rows["성수 충전소"][4], 0)
        # Prices equal to the operator matrix are not repeated per station.
        self.assertEqual(len(rows["성수 충전소"]), 5)
        # Operators without a listed matrix keep their station without inventing prices.
        self.assertEqual(data["operators"][rows["시청 주차장"][2]], "익산시")
        self.assertEqual(len(rows["시청 주차장"]), 5)

    def test_keeps_station_specific_prices(self):
        self.write([
            self.row(1, "할인 충전소", "서울특별시 중구 1", "채비", "100", [289, 315, 350, 350, 430]),
            self.row(2, "일반 충전소", "서울특별시 중구 2", "채비", "100", [289, 315, 410, 430, 430]),
        ])
        rows = {row[0]: row for row in parse(self.xlsx, self.rates)["stations"]}
        self.assertEqual(rows["할인 충전소"][5], [289.0, 315.0, 350.0, 350.0, 430.0])
        self.assertEqual(len(rows["일반 충전소"]), 5)

    def test_rejects_layout_changes_and_bad_outputs(self):
        self.write([self.row(1, "a", "b", "채비", "100", ME)] * 2, HEADER[:-1] + ["로밍여부"])
        with self.assertRaisesRegex(ValueError, "unexpected station fee layout"):
            parse(self.xlsx, self.rates)
        self.write([self.row(1, "a", "b", "채비", "백", ME), self.row(2, "c", "d", "채비", "7", ME)])
        with self.assertRaisesRegex(ValueError, "invalid charger output"):
            parse(self.xlsx, self.rates)

    def test_rejects_small_or_shrinking_exports(self):
        self.write([self.row(1, "a", "b", "채비", "100", ME)])
        with self.assertRaisesRegex(ValueError, "only 1 stations"):
            parse(self.xlsx, self.rates)
        self.write([self.row(1, "a", "b", "채비", "100", ME), self.row(2, "c", "d", "채비", "7", ME)])
        with self.assertRaisesRegex(ValueError, "below the previous 10"):
            parse(self.xlsx, self.rates, previous_count=10)

    def test_rejects_non_xlsx_download(self):
        self.xlsx.write_text("<html>점검 중</html>", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "expected an XLSX workbook"):
            parse(self.xlsx, self.rates)


if __name__ == "__main__":
    unittest.main()
