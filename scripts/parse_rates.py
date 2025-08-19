# scripts/parse_rates.py
import pandas as pd
import re
import json
import sys

in_path = sys.argv[1]
out_path = sys.argv[2]


def parse_rate_text(text):
    if pd.isna(text):
        return []
    text = str(text).replace("\n", " ")
    matches = re.findall(r"\(\s*(\d+)\s*~\s*(\d+)kW\)\s*([\d.,]+)원", text)
    rates = [
        {"min_kW": int(mn), "max_kW": int(mx), "price": float(price.replace(",", ""))}
        for mn, mx, price in matches
    ]
    rates.sort(key=lambda item: item["min_kW"])
    return rates


df = pd.read_excel(in_path)
charging_rates = {}

for _, row in df.iterrows():
    operator = str(row.iloc[0]).strip()
    charging_rates[operator] = {}
    for provider in df.columns[1:]:
        parsed = parse_rate_text(row[provider])
        if parsed:
            charging_rates[operator][provider] = parsed

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(charging_rates, f, ensure_ascii=False, sort_keys=True, separators=( ",", ":"))