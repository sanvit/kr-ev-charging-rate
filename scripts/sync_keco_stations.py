"""Build stations/map.json from the KECO charger API (data.go.kr 15076352).

Needs a data.go.kr service key in DATA_GO_KR_SERVICE_KEY (the *decoded* key).
The API lists one row per charger; this groups them into stations with
coordinates, the operator code (busiId, same codes as data/operator_codes.json)
and the installed outputs. The output is written only after every page has
been fetched and validated, so a failed run leaves the previous file alone.
"""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.parse_operator_fees import write_json_atomic


API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
SOURCE_URL = "https://www.data.go.kr/data/15076352/openapi.do"
PAGE_SIZE = 9999
MAX_PAGES = 200
MAX_DROP = 0.2
FIELDS = ["id", "name", "lat", "lng", "operator", "address", "outputs", "restricted", "useTime"]
# Mainland Korea, Jeju and Ulleung/Dokdo with a margin.
LAT_RANGE = (33.0, 38.9)
LNG_RANGE = (124.5, 132.0)


class SyncError(RuntimeError):
    pass


def parse_page(payload):
    """Return (total_count, items) for either documented JSON wrapper."""
    root = payload.get("response", payload) if isinstance(payload, dict) else None
    if not isinstance(root, dict):
        raise SyncError("unexpected response shape")
    header = root.get("header", root)
    body = root.get("body", root)
    if str(header.get("resultCode")) != "00":
        raise SyncError(f"API returned resultCode={header.get('resultCode')!r} ({header.get('resultMsg')!r})")
    try:
        total = int(header.get("totalCount", body.get("totalCount")))
    except (TypeError, ValueError) as exc:
        raise SyncError("missing totalCount") from exc
    items = (body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else body.get("items")
    if isinstance(items, dict):
        items = [items]
    if total <= 0 or not isinstance(items, list) or not items:
        raise SyncError("empty charger list")
    if not all(isinstance(item, dict) for item in items):
        raise SyncError("malformed charger rows")
    return total, items


def fetch_page(service_key, page_no, opener=urllib.request.urlopen, retries=6, region=None):
    query = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": PAGE_SIZE,
        "dataType": "JSON",
    }
    if region:
        query["zcode"] = region
    url = f"{API_URL}?{urllib.parse.urlencode(query)}"
    last_error = None
    for attempt in range(retries):
        try:
            with opener(url, timeout=120) as response:
                raw = response.read().decode("utf-8")
            if raw.lstrip().startswith("<"):
                # Key and quota errors come back as XML even when JSON is requested.
                raise SyncError(f"API returned XML instead of JSON: {raw.strip()[:200]}")
            return parse_page(json.loads(raw))
        except SyncError:
            raise
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            # Overseas callers (e.g. GitHub runners) see frequent TLS resets.
            time.sleep(min(10 * (attempt + 1), 60))
    raise SyncError(f"page {page_no} failed after {retries} attempts: {last_error}")


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def group_stations(rows):
    stations = {}
    seen = set()
    for row in rows:
        if str(row.get("delYn", "")).upper() == "Y":
            continue
        station_id = str(row.get("statId") or "").strip()
        charger_id = str(row.get("chgerId") or "").strip()
        operator = str(row.get("busiId") or "").strip()
        if not station_id or not charger_id or not operator:
            raise SyncError(f"charger row without statId/chgerId/busiId: {row!r}"[:300])
        if (station_id, charger_id) in seen:
            raise SyncError(f"duplicate charger {station_id}/{charger_id}; the list changed during paging")
        seen.add((station_id, charger_id))

        lat, lng = number(row.get("lat")), number(row.get("lng"))
        if lat is None or lng is None or not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
            continue
        output = number(row.get("output"))
        station = stations.setdefault(station_id, {
            "name": str(row.get("statNm") or "").strip(),
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "operator": operator,
            "operatorName": str(row.get("bnm") or "").strip(),
            "address": str(row.get("addr") or "").strip(),
            "outputs": set(),
            "restricted": False,
            "useTime": str(row.get("useTime") or "").strip(),
        })
        if output and output > 0:
            station["outputs"].add(int(output) if output.is_integer() else output)
        if str(row.get("limitYn", "")).upper() == "Y":
            station["restricted"] = True
    return stations


def build_payload(stations, codes):
    operators = {}
    rows = []
    for station_id in sorted(stations):
        station = stations[station_id]
        code = station["operator"]
        operators.setdefault(code, codes.get(code) or station["operatorName"] or code)
        rows.append([
            station_id,
            station["name"],
            station["lat"],
            station["lng"],
            code,
            station["address"],
            sorted(station["outputs"]),
            1 if station["restricted"] else 0,
            station["useTime"],
        ])
    return {
        "source": "한국환경공단 전기자동차 충전소 정보",
        "sourceUrl": SOURCE_URL,
        "license": "공공누리 제1유형 (출처표시)",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "count": len(rows),
        "fields": FIELDS,
        "operators": dict(sorted(operators.items())),
        "stations": rows,
    }


def sync(service_key, codes_path, out_path, opener=urllib.request.urlopen, region=None):
    codes = json.loads(Path(codes_path).read_text(encoding="utf-8"))["codes"]
    rows, expected = [], None
    for page_no in range(1, MAX_PAGES + 1):
        total, items = fetch_page(service_key, page_no, opener=opener, region=region)
        if expected is not None and total != expected:
            raise SyncError("totalCount changed during paging; run again later")
        expected = total
        rows.extend(items)
        print(f"page {page_no}: {len(rows)}/{expected} chargers")
        if len(rows) >= expected:
            break
    if len(rows) != expected:
        raise SyncError(f"fetched {len(rows)} chargers but the API reported {expected}")

    payload = build_payload(group_stations(rows), codes)
    if not payload["count"]:
        raise SyncError("no stations with valid coordinates")
    try:
        previous = json.loads(Path(out_path).read_text(encoding="utf-8")).get("count")
    except (OSError, ValueError):
        previous = None
    if previous and not region and payload["count"] < previous * (1 - MAX_DROP):
        raise SyncError(f"{payload['count']} stations is more than {MAX_DROP:.0%} below the previous {previous}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(payload, out_path)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_path", help="output JSON path, e.g. stations/map.json")
    parser.add_argument("--codes", default=Path(__file__).resolve().parent.parent / "data" / "operator_codes.json")
    parser.add_argument("--region", help="optional two-digit zcode for a partial test run (e.g. 11 = Seoul)")
    args = parser.parse_args()
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        parser.exit(1, "DATA_GO_KR_SERVICE_KEY is not set; keeping the previous station map.\n")
    if args.region and not (len(args.region) == 2 and args.region.isdigit()):
        parser.exit(1, "--region must be a two-digit zcode\n")
    try:
        payload = sync(key, args.codes, args.out_path, region=args.region)
    except (OSError, SyncError) as exc:
        parser.exit(1, f"Failed to sync KECO stations: {exc}\n")
    print(f"Wrote {payload['count']} stations for {len(payload['operators'])} operators")


if __name__ == "__main__":
    main()
