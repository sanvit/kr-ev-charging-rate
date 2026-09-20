"""Convert ev.or.kr's five roaming fee workbooks to charging_rates.json."""

import argparse
import json
import math
from pathlib import Path
import tempfile
from zipfile import is_zipfile

import pandas as pd


# The source exports one matrix per power band. Keep the existing inclusive,
# integer min_kW/max_kW schema; 9999 represents the open-ended 200kW+ band.
STAGES = (
    ("s1", 0, 29),
    ("s2", 30, 49),
    ("s3", 50, 99),
    ("s4", 100, 199),
    ("s5", 200, 9999),
)


def parse_rates(in_dir):
    charging_rates = {}
    expected_axes = None

    for stage, min_kw, max_kw in STAGES:
        path = Path(in_dir) / f"{stage}.xlsx"
        if not path.is_file():
            raise ValueError(f"Missing power-band workbook: {path}")
        if not is_zipfile(path):
            raise ValueError(f"{path}: expected an XLSX workbook; check the download response")

        df = pd.read_excel(path, engine="openpyxl").dropna(how="all")
        if df.empty or len(df.columns) < 2 or df.columns[0] != "발급/이용":
            raise ValueError(f"{path}: unexpected roaming fee matrix layout")

        if df.iloc[:, 0].isna().any():
            raise ValueError(f"{path}: missing card issuer name")
        issuers = [str(value).strip() for value in df.iloc[:, 0]]
        providers = [str(value).strip() for value in df.columns[1:]]
        if not all(issuers + providers) or len(set(issuers)) != len(issuers):
            raise ValueError(f"{path}: empty or duplicate business names")

        axes = (set(issuers), set(providers))
        if expected_axes is not None and axes != expected_axes:
            raise ValueError(f"{path}: businesses differ between power-band workbooks")
        expected_axes = axes

        count = 0
        for issuer, row in zip(issuers, df.itertuples(index=False, name=None)):
            charging_rates.setdefault(issuer, {})
            for provider, value in zip(providers, row[1:]):
                if pd.isna(value) or str(value).strip() in ("", "-"):
                    continue
                try:
                    price = float(str(value).strip().replace(",", ""))
                except ValueError as exc:
                    raise ValueError(f"{path}: invalid fee for {issuer} / {provider}: {value!r}") from exc
                if not math.isfinite(price) or price < 0:
                    raise ValueError(f"{path}: invalid fee for {issuer} / {provider}: {value!r}")
                # ev.or.kr displays zero as unavailable, not as free charging.
                if price == 0:
                    continue
                charging_rates[issuer].setdefault(provider, []).append(
                    {"min_kW": min_kw, "max_kW": max_kw, "price": price}
                )
                count += 1

        if count == 0:
            raise ValueError(f"{path}: no charging rates found; refusing to replace existing data")

    return charging_rates


def generate_json(in_dir, out_path):
    rates = parse_rates(in_dir)
    out_path = Path(out_path)
    # Finish parsing every band before replacing the last valid JSON.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=out_path.parent, delete=False
    ) as output:
        temporary_path = Path(output.name)
        try:
            json.dump(rates, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            output.close()
            temporary_path.replace(out_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("in_dir", help="directory containing s1.xlsx through s5.xlsx")
    parser.add_argument("out_path", help="output JSON path")
    args = parser.parse_args()
    try:
        rates = generate_json(args.in_dir, args.out_path)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Failed to generate charging rates: {exc}\n")
    count = sum(len(bands) for providers in rates.values() for bands in providers.values())
    print(f"Generated {count} rates for {len(rates)} card issuers")


if __name__ == "__main__":
    main()
