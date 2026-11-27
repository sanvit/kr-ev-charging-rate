"""Convert ev.or.kr's station fee export to a compact station directory.

The export (selectEvStationFeeExcel.do, feeGb=M) lists every station with a
registered price: name, address, the operator (CPO) running it, the charger
outputs installed there and whether roaming is accepted. ev.or.kr fills the
price columns from the operator's member matrix, so the directory stores the
operator link and only keeps a station's own prices when they differ from the
matrix in operator_rates.json.
"""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from zipfile import is_zipfile

import pandas as pd

from scripts.parse_operator_fees import parse_price, write_json_atomic


SOURCE_URL = "https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do"
HEADER = [
    "번호",
    "충전소명",
    "주소",
    "사업자 (CPO)",
    "충전기 출력구성 (kW)",
    "완속(30kW 미만)",
    "중속(30~49kW)",
    "급속(50~99kW)",
    "급속(100~199kW)",
    "초급속(200kW 이상)",
    "로밍",
]
FIELDS = ["name", "address", "operator", "outputs", "roaming", "prices"]
# The live export has ~100k stations; anything this small is a broken download.
MIN_STATIONS = 10_000
# A sudden large drop is more likely a partial export than real closures.
MAX_DROP = 0.2


def text(value):
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value) else str(value).strip()


def parse_outputs(value, path, name):
    outputs = []
    for part in text(value).replace("·", ",").split(","):
        part = part.strip()
        if part in ("", "-"):
            continue
        try:
            kw = float(part)
        except ValueError as exc:
            raise ValueError(f"{path}: invalid charger output for {name}: {value!r}") from exc
        if not math.isfinite(kw) or kw <= 0:
            raise ValueError(f"{path}: invalid charger output for {name}: {value!r}")
        outputs.append(int(kw) if kw.is_integer() else kw)
    return sorted(set(outputs))


def parse_station_fees(xlsx_path, operator_rates_path, previous_count=None):
    path = Path(xlsx_path)
    if not path.is_file():
        raise ValueError(f"Missing station fee workbook: {path}")
    if not is_zipfile(path):
        raise ValueError(f"{path}: expected an XLSX workbook; check the download response")
    df = pd.read_excel(path, engine="openpyxl", dtype=str).dropna(how="all")
    if [str(column).strip() for column in df.columns] != HEADER:
        raise ValueError(f"{path}: unexpected station fee layout")

    operator_rates = json.loads(Path(operator_rates_path).read_text(encoding="utf-8"))["operators"]

    merged = {}
    for record in df.itertuples(index=False, name=None):
        name, address, operator = text(record[1]), text(record[2]), text(record[3])
        if not name or not operator:
            raise ValueError(f"{path}: station without a name or operator: {record!r}")
        outputs = parse_outputs(record[4], path, name)
        prices = [parse_price(value, path, name) for value in record[5:10]]
        roaming = text(record[10]) == "있음"
        listed = operator_rates.get(operator, {}).get("member")
        own_prices = None if prices == listed or (listed is None and not any(prices)) else prices

        key = (name, address, operator)
        if key in merged:
            # The export splits one site into rows per output group.
            station = merged[key]
            station["outputs"] = sorted(set(station["outputs"]) | set(outputs))
            station["roaming"] = station["roaming"] or roaming
            station["prices"] = station["prices"] or own_prices
        else:
            merged[key] = {"outputs": outputs, "roaming": roaming, "prices": own_prices}

    count = len(merged)
    if count < MIN_STATIONS:
        raise ValueError(f"{path}: only {count} stations; refusing to replace existing data")
    if previous_count and count < previous_count * (1 - MAX_DROP):
        raise ValueError(
            f"{path}: {count} stations is more than {MAX_DROP:.0%} below the previous {previous_count}; "
            "check the export before replacing the directory"
        )

    operators = sorted({operator for _, _, operator in merged}, key=lambda value: value.encode("utf-8"))
    index = {operator: position for position, operator in enumerate(operators)}
    stations = []
    for (name, address, operator), station in sorted(merged.items()):
        row = [name, address, index[operator], station["outputs"], 1 if station["roaming"] else 0]
        if station["prices"] is not None:
            row.append(station["prices"])
        stations.append(row)
    return {
        "source": SOURCE_URL,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "count": len(stations),
        "fields": FIELDS,
        "operators": operators,
        "stations": stations,
    }


def previous_station_count(out_path):
    try:
        return json.loads(Path(out_path).read_text(encoding="utf-8")).get("count")
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx_path", help="station fee workbook (feeGb=M)")
    parser.add_argument("operator_rates", help="operator_rates.json generated from the same day's matrices")
    parser.add_argument("out_path", help="output JSON path")
    args = parser.parse_args()
    try:
        data = parse_station_fees(args.xlsx_path, args.operator_rates, previous_station_count(args.out_path))
        Path(args.out_path).parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(data, args.out_path)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Failed to generate station directory: {exc}\n")
    overrides = sum(len(row) > 5 for row in data["stations"])
    print(f"Generated {data['count']} stations for {len(data['operators'])} operators ({overrides} with station prices)")


if __name__ == "__main__":
    main()
