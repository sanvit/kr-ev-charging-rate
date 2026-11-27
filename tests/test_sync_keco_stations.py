import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.parse

from scripts import sync_keco_stations
from scripts.sync_keco_stations import SyncError, parse_page, sync


def charger(stat, chger, busi="ME", lat="37.5665", lng="126.978", output="100", **extra):
    return {
        "statId": stat, "chgerId": chger, "busiId": busi, "bnm": "기관명", "statNm": f"{stat} 충전소",
        "addr": "서울특별시 중구 세종대로 110", "lat": lat, "lng": lng, "output": output,
        "useTime": "24시간 이용가능", "limitYn": "N", "delYn": "N", **extra,
    }


def flat_page(items, total):
    return {"resultCode": "00", "resultMsg": "NORMAL SERVICE.", "totalCount": total, "items": {"item": items}}


def nested_page(items, total):
    return {"response": {"header": {"resultCode": "00", "totalCount": total}, "body": {"items": {"item": items}}}}


class FakeOpener:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["pageNo"][0])
        body = self.pages[page - 1]
        return io.BytesIO((body if isinstance(body, str) else json.dumps(body)).encode("utf-8"))


class SyncKecoStationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.codes = self.directory / "codes.json"
        self.codes.write_text(json.dumps({"codes": {"ME": "기후에너지환경부", "PI": "GS차지비"}}, ensure_ascii=False), encoding="utf-8")
        self.output = self.directory / "stations" / "map.json"
        patcher = mock.patch.object(sync_keco_stations.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_groups_chargers_into_stations_across_pages(self):
        opener = FakeOpener([
            flat_page([charger("ME000001", "01"), charger("ME000001", "02", output="7")], 4),
            nested_page([
                charger("PI000002", "01", busi="PI", limitYn="Y", lat="35.1796", lng="129.0756", output="200"),
                charger("ME000003", "01", delYn="Y"),
            ], 4),
        ])
        payload = sync("secret-key", self.codes, self.output, opener=opener)
        self.assertIn("serviceKey=secret-key", opener.urls[0])
        self.assertIn("dataType=JSON", opener.urls[0])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["operators"], {"ME": "기후에너지환경부", "PI": "GS차지비"})
        rows = {row[0]: dict(zip(payload["fields"], row)) for row in payload["stations"]}
        self.assertEqual(rows["ME000001"]["outputs"], [7, 100])
        self.assertEqual(rows["ME000001"]["operator"], "ME")
        self.assertEqual(rows["PI000002"]["restricted"], 1)
        self.assertEqual(rows["PI000002"]["lat"], 35.1796)
        written = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(written["count"], 2)
        self.assertNotIn("secret-key", self.output.read_text(encoding="utf-8"))

    def test_skips_rows_outside_korea_but_keeps_counting(self):
        opener = FakeOpener([flat_page([charger("ME1", "01"), charger("ME2", "01", lat="0", lng="0")], 2)])
        payload = sync("k", self.codes, self.output, opener=opener)
        self.assertEqual([row[0] for row in payload["stations"]], ["ME1"])

    def test_xml_errors_and_bad_result_codes_fail_without_writing(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text('{"count": 1}', encoding="utf-8")
        xml = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
        with self.assertRaisesRegex(SyncError, "XML instead of JSON"):
            sync("k", self.codes, self.output, opener=FakeOpener([xml]))
        with self.assertRaisesRegex(SyncError, "resultCode='30'"):
            sync("k", self.codes, self.output, opener=FakeOpener([{"resultCode": "30", "resultMsg": "bad key"}]))
        self.assertEqual(self.output.read_text(encoding="utf-8"), '{"count": 1}')

    def test_detects_list_changes_while_paging(self):
        with self.assertRaisesRegex(SyncError, "totalCount changed"):
            sync("k", self.codes, self.output, opener=FakeOpener([
                flat_page([charger("ME1", "01")], 2), flat_page([charger("ME2", "01")], 3),
            ]))
        with self.assertRaisesRegex(SyncError, "duplicate charger"):
            sync("k", self.codes, self.output, opener=FakeOpener([
                flat_page([charger("ME1", "01")], 2), flat_page([charger("ME1", "01")], 2),
            ]))

    def test_refuses_large_drop(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text('{"count": 100}', encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "below the previous 100"):
            sync("k", self.codes, self.output, opener=FakeOpener([flat_page([charger("ME1", "01")], 1)]))

    def test_parse_page_rejects_empty_lists(self):
        with self.assertRaisesRegex(SyncError, "empty charger list"):
            parse_page({"resultCode": "00", "totalCount": 0, "items": {"item": []}})


if __name__ == "__main__":
    unittest.main()
