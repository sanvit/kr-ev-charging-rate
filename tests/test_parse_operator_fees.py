import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.parse_operator_fees import HEADER, MIN_OPERATORS, generate_json


def operator_rows(count, first=None):
    rows = [first] if first else []
    for index in range(len(rows), count):
        rows.append([f"사업자{index:03d}", 300, 310, 320, 330, "-", "2026-03-13"])
    return rows


class ParseOperatorFeesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.codes = self.directory / "codes.json"
        self.codes.write_text(json.dumps({"codes": {"PI": "GS차지비"}}, ensure_ascii=False), encoding="utf-8")
        self.output = self.directory / "operator_rates.json"
        self.output.write_text('{"previous":"data"}', encoding="utf-8")
        self.write("member.xlsx", operator_rows(MIN_OPERATORS, ["GS차지비", 295, 307, "325", "345", "-", "2026-09-22"]))
        self.write("non_member.xlsx", operator_rows(MIN_OPERATORS, ["GS차지비", 470, 470, 470, 470, None, "2026-09-22"]))

    def write(self, name, rows, header=HEADER):
        pd.DataFrame(rows, columns=header).to_excel(self.directory / name, index=False)

    def assert_rejected(self, message):
        previous = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, message):
            generate_json(self.directory, self.codes, self.output)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_combines_member_and_non_member_prices_with_codes(self):
        generate_json(self.directory, self.codes, self.output)
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual([band["id"] for band in data["bands"]], ["s1", "s2", "s3", "s4", "s5"])
        self.assertEqual(data["operators"]["GS차지비"], {
            "code": "PI",
            "updated": "2026-09-22",
            "member": [295.0, 307.0, 325.0, 345.0, None],
            "nonMember": [470.0, 470.0, 470.0, 470.0, None],
        })
        self.assertIsNone(data["operators"]["사업자001"]["code"])
        first = self.output.read_bytes()
        generate_json(self.directory, self.codes, self.output)
        self.assertEqual(self.output.read_bytes(), first, "output must be stable so unchanged data makes no commit")

    def test_zero_is_unlisted_not_free(self):
        self.write("member.xlsx", operator_rows(MIN_OPERATORS, ["GS차지비", 0, 307, 325, 345, "-", "2026-09-22"]))
        data = generate_json(self.directory, self.codes, self.output)
        self.assertIsNone(data["operators"]["GS차지비"]["member"][0])

    def test_rejects_changed_layout(self):
        header = HEADER[:-1] + ["변경일"]
        self.write("member.xlsx", operator_rows(MIN_OPERATORS), header)
        self.assert_rejected("unexpected operator fee matrix layout")

    def test_rejects_html_error_page(self):
        (self.directory / "member.xlsx").write_text("<html>error</html>", encoding="utf-8")
        self.assert_rejected("expected an XLSX workbook")

    def test_rejects_truncated_export(self):
        self.write("member.xlsx", operator_rows(3))
        self.write("non_member.xlsx", operator_rows(3))
        self.assert_rejected("only 3 operators")

    def test_rejects_mismatched_operator_lists(self):
        self.write("non_member.xlsx", operator_rows(MIN_OPERATORS + 1, ["GS차지비", 470, 470, 470, 470, None, "2026-09-22"]))
        self.assert_rejected("different operators")

    def test_rejects_bad_prices_and_dates(self):
        self.write("member.xlsx", operator_rows(MIN_OPERATORS, ["GS차지비", "abc", 1, 1, 1, 1, "2026-09-22"]))
        self.assert_rejected("invalid fee for GS차지비")
        self.write("member.xlsx", operator_rows(MIN_OPERATORS, ["GS차지비", 1, 1, 1, 1, 1, "어제"]))
        self.assert_rejected("invalid update date")

    def test_attaches_variant_notes_and_ignores_a_broken_notes_file(self):
        variants = self.directory / "variants.xls"
        # The live file is legacy .xls; the parser only needs a pandas-readable workbook.
        rows = [
            ["전기차 충전 요금(2026-11-27)"] + [None] * 7,
            [None] * 8,
            ["순번", "사업자명", "요금유형", "용량구분", "회원가", None, "비회원가", "갱신일자"],
            [None, None, None, None, "충전요금(원)", "비고", "충전요금(원)", None],
            ["1", "GS차지비", "대표", "완속", "295", None, "470", "2026-09-22"],
            ["2", "GS차지비", "변동", "완속②", "275", "야간", "-", "2026-09-22"],
            ["3", "사업자001", "대표", "급속(100kW이상)", "330", "비회원가 없음", "-", "2026-03-13"],
        ]
        pd.DataFrame(rows).to_excel(variants.with_suffix(".xlsx"), header=False, index=False)
        variants.with_suffix(".xlsx").rename(variants)
        data = generate_json(self.directory, self.codes, self.output)
        self.assertEqual(data["operators"]["GS차지비"]["notes"], [
            {"kind": "변동", "band": "완속", "member": 275.0, "nonMember": None, "note": "야간"},
        ])
        self.assertEqual(data["operators"]["사업자001"]["notes"][0]["note"], "비회원가 없음")
        self.assertNotIn("notes", data["operators"]["사업자002"])

        variants.write_text("<html>error</html>", encoding="utf-8")
        data = generate_json(self.directory, self.codes, self.output)
        self.assertNotIn("notes", data["operators"]["GS차지비"])

    def test_rejects_duplicate_operator(self):
        rows = operator_rows(MIN_OPERATORS)
        rows.append(list(rows[0]))
        self.write("member.xlsx", rows)
        self.assert_rejected("duplicate operator")


if __name__ == "__main__":
    unittest.main()
