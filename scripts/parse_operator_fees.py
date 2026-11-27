"""Convert ev.or.kr's operator fee matrices to operator_rates.json.

ev.or.kr publishes each charging operator's own price per power band twice:
`feeGb=M` is the member price (the operator's own card or app) and `feeGb=N`
is the non-member price (paying at the charger without that operator's
membership). Each row also carries the date the operator last updated it.
"""

import argparse
from datetime import date
import json
import math
from pathlib import Path
import tempfile
from zipfile import is_zipfile

import pandas as pd


SOURCE_URL = "https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do"
# Same inclusive integer-kW convention as charging_rates.json.
BANDS = (
    {"id": "s1", "min_kW": 0, "max_kW": 29},
    {"id": "s2", "min_kW": 30, "max_kW": 49},
    {"id": "s3", "min_kW": 50, "max_kW": 99},
    {"id": "s4", "min_kW": 100, "max_kW": 199},
    {"id": "s5", "min_kW": 200, "max_kW": 9999},
)
HEADER = [
    "사업자",
    "완속(30kW 미만)",
    "중속(30~49kW)",
    "급속(50~99kW)",
    "급속(100~199kW)",
    "초급속(200kW 이상)",
    "갱신일",
]
KINDS = (("member", "member.xlsx"), ("nonMember", "non_member.xlsx"))
# The live export lists ~100 operators. A much shorter table means a broken
# or partial response, so keep the previous file instead.
MIN_OPERATORS = 20


def parse_price(value, path, operator):
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value).strip()
    if text in ("", "-"):
        return None
    try:
        price = float(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"{path}: invalid fee for {operator}: {value!r}") from exc
    if not math.isfinite(price) or price < 0:
        raise ValueError(f"{path}: invalid fee for {operator}: {value!r}")
    # ev.or.kr shows zero as "not listed", never as free charging.
    return price if price > 0 else None


def read_matrix(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Missing operator fee workbook: {path}")
    if not is_zipfile(path):
        raise ValueError(f"{path}: expected an XLSX workbook; check the download response")
    df = pd.read_excel(path, engine="openpyxl", dtype=str).dropna(how="all")
    if [str(column).strip() for column in df.columns] != HEADER:
        raise ValueError(f"{path}: unexpected operator fee matrix layout")

    rows = {}
    for record in df.itertuples(index=False, name=None):
        operator = "" if pd.isna(record[0]) else str(record[0]).strip()
        if not operator:
            raise ValueError(f"{path}: missing operator name")
        if operator in rows:
            raise ValueError(f"{path}: duplicate operator {operator}")
        prices = [parse_price(value, path, operator) for value in record[1:6]]
        try:
            updated = date.fromisoformat(str(record[6]).strip()).isoformat()
        except ValueError as exc:
            raise ValueError(f"{path}: invalid update date for {operator}: {record[6]!r}") from exc
        rows[operator] = {"prices": prices, "updated": updated}
    if len(rows) < MIN_OPERATORS:
        raise ValueError(f"{path}: only {len(rows)} operators; refusing to replace existing data")
    return rows


VARIANT_HEADER = ["순번", "사업자명", "요금유형", "용량구분", "회원가", None, "비회원가", "갱신일자"]
CIRCLED = str.maketrans("", "", "①②③④⑤⑥⑦⑧⑨⑩")


def read_variants(path):
    """Notes and non-standard tariffs from selectEvcarStationPriceExcel.do.

    The band matrices keep only each operator's representative (대표) price.
    This older export also lists time-of-use (변동), business (사업차등) and
    regional (지역차등) variants plus remarks such as "비회원가 없음", which the
    site shows as caveats next to the matrix price.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Missing tariff variant workbook: {path}")
    df = pd.read_excel(path, header=None, dtype=str)
    header = [None if pd.isna(value) else str(value).strip() for value in df.iloc[2].tolist()]
    if header != VARIANT_HEADER:
        raise ValueError(f"{path}: unexpected tariff variant layout")
    notes = {}
    for record in df.iloc[4:].itertuples(index=False, name=None):
        number = "" if pd.isna(record[0]) else str(record[0]).strip()
        if not number.isdigit():
            continue
        operator, kind, band = (("" if pd.isna(v) else str(v).strip()) for v in record[1:4])
        remark = "" if pd.isna(record[5]) else str(record[5]).strip()
        if not operator or (kind == "대표" and not remark):
            continue
        notes.setdefault(operator, []).append({
            "kind": kind,
            "band": band.translate(CIRCLED).strip(),
            "member": parse_price(record[4], path, operator),
            "nonMember": parse_price(record[6], path, operator),
            "note": remark,
        })
    if not notes:
        raise ValueError(f"{path}: no tariff notes found")
    return notes


def load_codes(path):
    codes = json.loads(Path(path).read_text(encoding="utf-8"))["codes"]
    by_name = {}
    for code, name in codes.items():
        if name in by_name:
            raise ValueError(f"{path}: operator {name} has two codes")
        by_name[name] = code
    return by_name


def parse_operator_fees(in_dir, codes_path):
    matrices = {kind: read_matrix(Path(in_dir) / filename) for kind, filename in KINDS}
    if set(matrices["member"]) != set(matrices["nonMember"]):
        raise ValueError("member and non-member matrices list different operators")
    codes = load_codes(codes_path)
    variants_path = Path(in_dir) / "variants.xls"
    notes = {}
    if variants_path.exists():
        try:
            notes = read_variants(variants_path)
        except (OSError, ValueError, ImportError) as exc:
            # Notes are supplementary; never block the price update on them.
            print(f"warning: skipping tariff notes: {exc}")

    operators = {}
    for name in sorted(matrices["member"]):
        member = matrices["member"][name]
        non_member = matrices["nonMember"][name]
        operators[name] = {
            "code": codes.get(name),
            # Both exports carry the same date today; keep the newer one if they diverge.
            "updated": max(member["updated"], non_member["updated"]),
            "member": member["prices"],
            "nonMember": non_member["prices"],
        }
        if notes.get(name):
            operators[name]["notes"] = notes[name]
    if not any(price is not None for item in operators.values() for price in item["member"]):
        raise ValueError("no member prices found; refusing to replace existing data")
    return {"source": SOURCE_URL, "bands": list(BANDS), "operators": operators}


def write_json_atomic(data, out_path):
    out_path = Path(out_path)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=out_path.parent, delete=False
    ) as output:
        temporary_path = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            output.close()
            temporary_path.replace(out_path)
        finally:
            temporary_path.unlink(missing_ok=True)


def generate_json(in_dir, codes_path, out_path):
    data = parse_operator_fees(in_dir, codes_path)
    write_json_atomic(data, out_path)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("in_dir", help="directory containing member.xlsx, non_member.xlsx and optionally variants.xls")
    parser.add_argument("out_path", help="output JSON path")
    parser.add_argument("--codes", default=Path(__file__).resolve().parent.parent / "data" / "operator_codes.json")
    args = parser.parse_args()
    try:
        data = generate_json(args.in_dir, args.codes, args.out_path)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Failed to generate operator rates: {exc}\n")
    print(f"Generated member/non-member rates for {len(data['operators'])} operators")


if __name__ == "__main__":
    main()
