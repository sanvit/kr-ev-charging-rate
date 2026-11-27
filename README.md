# Korean EV charging rates

GitHub Actions mirrors the public price tables of the
[무공해차 통합누리집](https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do)
(ev.or.kr) as JSON. The site that reads them is
[kr-ev-charging-rate-frontend](https://github.com/sanvit/kr-ev-charging-rate-frontend).

## Files

| File | Branch | Updated | What it holds |
| --- | --- | --- | --- |
| `charging_rates.json` | `main` | hourly | Roaming prices: card issuer → charging operator → power bands |
| `operator_rates.json` | `main` | hourly | Each operator's own member and non-member price per band, its update date and remarks |
| `stations/directory.json` | `data` | daily | ~100k stations listed on ev.or.kr: name, address, operator, charger outputs, roaming accepted |
| `stations/map.json` | `data` | daily, needs a key | Station coordinates from the KECO API, keyed by operator code |
| `data/operator_codes.json` | `main` | by hand | Operator code (`busiId`) → name, as used by ev.or.kr and the KECO API |

Read them from `https://raw.githubusercontent.com/sanvit/kr-ev-charging-rate/refs/heads/<branch>/<file>`
(served gzipped with `Access-Control-Allow-Origin: *`).

The `data` branch is rewritten as a single commit on every run, so ~10 MB of
daily station data never accumulates in `main`'s history.

### `charging_rates.json`

Unchanged format. Keys are card issuer names, then charging operator names, as
supplied by the export. Each pair has a list of `{min_kW, max_kW, price}`
ordered by power. Bounds are inclusive integer kW; `9999` is the upper sentinel
of the 200 kW+ band. Prices are KRW/kWh. Blank, `-` and zero cells mean "not
listed" in the source and are omitted.

The source matrix contains a few cells that look like typos (for example
30원/kWh for 카카오모빌리티 cards at GS차지비, where every other card pays
250원 or more). This file mirrors the source as is; the site flags such values
instead of ranking them.

### `operator_rates.json`

```json
{
  "source": "https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do",
  "bands": [{ "id": "s1", "min_kW": 0, "max_kW": 29 }, "… s2–s5"],
  "operators": {
    "GS차지비": {
      "code": "PI",
      "updated": "2026-09-22",
      "member": [295, 307, 325, 345, null],
      "nonMember": [470, 470, 470, 470, null]
    },
    "소프트베리": {
      "code": "SB",
      "updated": "2026-03-13",
      "member": [297, 297, null, null, null],
      "nonMember": [null, null, null, null, null],
      "notes": [{ "kind": "변동", "band": "완속", "member": 275, "nonMember": null, "note": "야간(비회원가 없음)" }]
    }
  }
}
```

`member`/`nonMember` follow the five bands in order; `null` means not listed.
`notes` appear only when ev.or.kr lists a remark or a non-representative tariff
(변동 time-of-use, 사업차등, 지역차등) for that operator.

## Sources

All exports below are the download buttons on ev.or.kr's price page. They need
no key. ev.or.kr serves its certificate without the intermediate
(TuringSign RSA Secure CA 3); GitHub's runners accept it, but some local
setups need that intermediate added to curl's CA bundle.

| Export | Request | Used for |
| --- | --- | --- |
| Roaming matrix | `POST selectEvRoamFeeExcel.do` `excelDown=Y&stage=s1…s5` (all five; no `stage` returns only 50–99 kW) | `charging_rates.json` |
| Operator matrix | `POST selectEvBusiFeeExcel.do` `excelDown=Y&feeGb=M` (member) / `feeGb=N` (non-member) `&speeds=s1,s2,s3,s4,s5` | `operator_rates.json` |
| Tariff list with remarks | `GET selectEvcarStationPriceExcel.do` (.xls) | `notes` in `operator_rates.json` (optional; skipped if it breaks) |
| Station fee list | `POST selectEvStationFeeExcel.do` `excelDown=Y&feeGb=M&speeds=s1,s2,s3,s4,s5` (~7 MB, ~35 s) | `stations/directory.json` |

All are under `https://ev.or.kr/nportal/evcarInfo/`.

ev.or.kr's own station map loads its data from `monitor/evMapList.do`, whose
response says "크롤링하지 말고 공공데이터포털을 이용해 주세요". This project
does not use it. Coordinates come from the official API instead:

- **한국환경공단 전기자동차 충전소 정보** ([data.go.kr 15076352](https://www.data.go.kr/data/15076352/openapi.do)),
  free, auto-approved, 1,000 calls/day on a development key, 공공누리 제1유형.
  `getChargerInfo` returns one row per charger with `busiId`, which uses the
  same codes as `data/operator_codes.json`. A full sync takes ~55 pages.

### Turning on station coordinates

1. Apply for the API on data.go.kr and copy the **decoded** (일반 인증키 Decoding) service key.
2. Add it as the repository secret `DATA_GO_KR_SERVICE_KEY`
   (Settings → Secrets and variables → Actions).
3. Run **Update Station Data** from the Actions tab.

Without the secret the workflow still publishes `stations/directory.json`.
Requests to data.go.kr from overseas IPs are often reset during the TLS
handshake, so the sync retries each page with backoff; if it still fails, the
previous `map.json` stays published. This sync has been tested against mocked
responses only, because no key was available when it was written.

## Workflows

- **Update Charging Rates** (hourly, and on changes to its scripts): tests,
  downloads the roaming and operator matrices, writes both JSON files and
  commits only when something changed.
- **Update Station Data** (daily 03:17 KST, or manually): tests, downloads the
  station list, optionally syncs KECO coordinates, then force-pushes a single
  commit to `data` when the contents changed.

Every parser checks the workbook layout, refuses suspiciously small or shrinking
exports, and writes atomically, so a broken download keeps the last good file.

## Running locally

```sh
python -m pip install pandas openpyxl xlrd
python -m unittest discover -s tests -v

mkdir -p rates operator-fees
for stage in s1 s2 s3 s4 s5; do
  curl --fail -sS --data "excelDown=Y&stage=${stage}" -o "rates/${stage}.xlsx" \
    https://ev.or.kr/nportal/evcarInfo/selectEvRoamFeeExcel.do
done
for kind in M:member N:non_member; do
  curl --fail -sS --data "excelDown=Y&feeGb=${kind%%:*}&speeds=s1,s2,s3,s4,s5" \
    -o "operator-fees/${kind#*:}.xlsx" https://ev.or.kr/nportal/evcarInfo/selectEvBusiFeeExcel.do
done
curl --fail -sS -o operator-fees/variants.xls https://ev.or.kr/nportal/evcarInfo/selectEvcarStationPriceExcel.do

python scripts/parse_rates.py rates charging_rates.json
python -m scripts.parse_operator_fees operator-fees operator_rates.json

curl --fail -sS --max-time 300 --data "excelDown=Y&feeGb=M&speeds=s1,s2,s3,s4,s5" \
  -o stations.xlsx https://ev.or.kr/nportal/evcarInfo/selectEvStationFeeExcel.do
python -m scripts.parse_station_fees stations.xlsx operator_rates.json out/stations/directory.json

# Optional, needs the data.go.kr key; --region 11 limits it to Seoul for a quick test.
DATA_GO_KR_SERVICE_KEY=... python -m scripts.sync_keco_stations out/stations/map.json --region 11
```
