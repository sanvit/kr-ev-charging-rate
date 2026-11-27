# Station data (generated)

This branch is rewritten by the **Update Station Data** workflow on `main`.
It always holds a single commit, so do not base work on it.

| File | Source | Contents |
| --- | --- | --- |
| `stations/directory.json` | [ev.or.kr 충전소 요금](https://ev.or.kr/nportal/evcarInfo/initEvcarChargePriceV2.do) export | Every station with a registered price: name, address, operator (CPO), charger outputs (kW), roaming accepted. No coordinates. |
| `stations/map.json` | [한국환경공단 충전소 정보 API](https://www.data.go.kr/data/15076352/openapi.do), 공공누리 제1유형 | Station coordinates grouped from charger rows, with the operator code (`busiId`). Present only when the `DATA_GO_KR_SERVICE_KEY` secret is set. |

Both files use `fields` + array rows to stay small. Operator prices are not
repeated per station; join `operator` with `operator_rates.json` on `main`.
A station row has a sixth `prices` element only when ev.or.kr lists a price
for that station that differs from its operator's member matrix.
