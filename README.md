# Korean EV charging rates

GitHub Actions downloads the roaming fee matrices from the
[무공해차 통합누리집](https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do)
and updates `charging_rates.json` hourly. It also runs when the workflow,
parser, or tests change on `main`. You can run **Update Charging Rates**
manually from the repository's Actions tab.

The current export endpoint is
`https://ev.or.kr/nportal/evcarInfo/selectEvRoamFeeExcel.do`.
It accepts a POST with `excelDown=Y` and a `stage` value. All five stages are
required: `s1` (below 30 kW), `s2` (30–49 kW), `s3` (50–99 kW),
`s4` (100–199 kW), and `s5` (200 kW and above). Omitting `stage` returns only
the 50–99 kW matrix.

JSON keys are card issuer names, then charging provider names, as supplied
by the export. Each pair contains a list of `{min_kW, max_kW, price}` objects,
ordered by power. Bounds follow the existing inclusive integer-kW convention;
`9999` is the upper sentinel for the open-ended 200 kW+ band. Prices are in
KRW/kWh. Blank, `-`, and zero cells mean unavailable in the source and are omitted.

To generate the JSON locally:

```sh
python -m pip install pandas openpyxl
mkdir -p rates
for stage in s1 s2 s3 s4 s5; do
  curl --fail --location --silent --show-error \
    --retry 3 --connect-timeout 10 --max-time 60 \
    --data "excelDown=Y&stage=${stage}" \
    --output "rates/${stage}.xlsx" \
    "https://ev.or.kr/nportal/evcarInfo/selectEvRoamFeeExcel.do"
done
python scripts/parse_rates.py rates charging_rates.json
```

Run regression tests with `python -m unittest discover -s tests -v`.
Invalid or incomplete exports fail before replacing the existing JSON.
