# 토스 시세·거래 API 연결 계약

기준: 2026-10-03. 공식 OpenAPI 1.2.19의 공개 명세와 현재 어댑터를 대조했습니다. 명세 사본은 `toss-openapi-reference-20261003.json`입니다. 키·토큰은 포함하지 않습니다.

## 원문

- [공식 REST 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
- [공식 스트림 명세](https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json)
- [Lightweight Charts 시리즈](https://tradingview.github.io/lightweight-charts/docs/series-types)
- [OpenDART 전체 재무제표](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)

## 데이터 계약

| 항목 | 실제 필드·조회 | 범위·주의점 | 우리 연결 |
|---|---|---|---|
| 가격 봉 | candles, OHLCV, `nextBefore`, 최대 200건 | `1m`·`1d`, adjusted 선택; 분할 조정 방법까지 추정하지 않음 | 5분·주·월은 실제 봉에서 집계, 누락 OHLC는 만들지 않음 |
| 투자자 수급 | investor-trading, `buyVolume/sellVolume/netBuyVolume`, `nextUntil` | KRX+NXT 통합, 단위 주, 금액 아님 | 개인·외국인·기관·기타법인 일별/누적 표 |
| 기관 세부 | institution.breakdown | 금융투자·보험·투신·사모·은행·기타금융·연기금 | 기관 상세 선택 |
| 외국인 보유 | foreignerHolding.holdingRate | 소수 비율 ×100, 순매수와 별개 | 일별 표 외국인 보유율 |
| 프로그램 | program-trades, arbitrage/nonArbitrage | KRX, 주 단위, 차익·비차익 하나라도 결측이면 전체도 결측 | 매수/매도/순매수, 기간별 누적 |
| 공매도 거래 | short-selling | 거래량·거래대금·비율, 순보유잔고가 아님 | 공매도 거래 탭; 잔고는 별도 원천 |
| 신용 | credit-trades, marginLoan/stockLoan | 날짜는 매매 기준일, 이후 발표; 잔고·신규·상환·비율 구분 | 융자와 대주 분리, 결제일로 오표시하지 않음 |
| 대차 | securities-lending | 발생량과 잔고 구분, 수량 주·금액 원 | 대차 추세·일별 표 |
| 호가·체결 | REST 스냅샷·WebSocket | 실제 응답 시장/시각 사용 | 공유 인증·구독, 재연결 복구 |

## 수집과 화면

### 공개 GET 20종 기능 대조표

각 응답은 `raw/toss/<sha256>.bin`과 메타데이터에 원본 그대로 보관한다. 내부 `toss-api:<경로>`에는 최근 시도 상태와 마지막 성공 시각·해시가 남는다. v75부터 동일 레코드에 세션별 HTTP 요청·성공·실패·상태 코드, 반환 행/바이트, 공급자 대기·응답·원본 보관 누계를 기록한다. 종목별 요청 파라미터와 인증값은 저장하지 않는다. `rows_received`는 API 응답 행 수이며 새로 저장된 행 수와 다르다. 세션은 worker 재시작 시 바뀌므로 서로 다른 세션 누계를 합쳐 처리량으로 계산하지 않는다. 아래 20종 모두 대표 실제 응답을 확인했으며 증거는 `toss-single-live-v22.json`이다. 빈 유의사항 목록은 정상 응답이고, 구독 승인 또는 마지막 페이지 수신을 장중 수신·전 종목 무결성 검증으로 대체하지 않는다.

| GET 경로 (`/api/v1` 아래) | 요청 조건 | 정규화·저장 | 화면 및 대조 |
|---|---|---|---|
| `/prices` | symbols 최대 200 | 통화·시각·양수·이전 시세 역전 검사 → quotes | 목록·종목 현재가 |
| `/orderbook` | symbol | 가격순 호가·잔량 → depth:code | 호가 표 |
| `/trades` | symbol | 실제 시각·가격·수량 → depth:code | 최근 체결, 매수/매도 추정 없음 |
| `/price-limits` | symbol | KRW·상하한가 → toss-limits:code | 종목 요약 |
| `/candles` | symbol·interval·count·before·adjusted | 실제 OHLCV → bars:code:통합:day/raw/1min, market_time_series | 선·영역·캔들·OHLC 및 숫자 표 |
| `/stocks` | symbols 최대 200 | 원천 종목 필드 → toss-stock:code | 회사 식별·거래정지 정보 보완 |
| `/stocks/all` | market·status | 원천 목록과 기존 상장 목록 대조 → toss-listing:market | 검색 범위 유지; 토스 미지원 종목 삭제 안 함 |
| `/stocks/{symbol}/warnings` | symbol | 유의사항·VI·거래소 → toss-warnings:code | 종목 상태 |
| `/stocks/{symbol}/investor-trading` | count·until | 투자자별 주 단위·외국인 보유율 → toss:investor-trading:code | 수급 표·기관 상세·누적·CSV |
| `/stocks/{symbol}/program-trades` | count·until | KRX 차익/비차익 주 단위 → toss:program-trades:code | 프로그램 표·순매수/매수/매도 |
| `/stocks/{symbol}/short-selling` | count·until | 거래 수량·금액·비율 → toss:short-selling:code, short-selling:code | 공매도 거래; 순보유잔고는 별도 원천 |
| `/stocks/{symbol}/credit-trades` | count·until | 융자/대주·신규/상환/잔고 → toss:credit-trades:code, financing:code | 신용 추세와 일별 표 |
| `/stocks/{symbol}/securities-lending` | count·until | 거래/잔고·주/원 분리 → toss:securities-lending:code, financing:code | 대차 추세와 일별 표 |
| `/exchange-rate` | USD·KRW | midRate·기준 시각 → toss-exchange-rate | 시장 금리·환율 |
| `/market-calendar/KR` | 없음 | 실제 세션 날짜·휴장 → toss-calendar:KR, market-session:date | 국내 장 일정·분석 공개 시점 |
| `/market-calendar/US` | 없음 | 원천 달력 → toss-calendar:US | 해외 장 일정 참고; 미국 종목 기능 없음 |
| `/rankings` | 6유형·기간·KR·주의종목 제외 | 원천 순위·시장전체/토스고객 구분 → toss-ranking:type:duration:exclude | 기간별 순위; 시가총액 자체 순위 유지 |
| `/market-indicators/prices` | KOSPI·KOSDAQ·국채 6종 | 포인트/금리 % 구분 → toss-market-indicators | 지수·국채 |
| `/market-indicators/{symbol}/candles` | interval·count·before | 음수 금리 허용·OHLC 검증 → toss-index:symbol:interval | 지수·금리 기간 차트 |
| `/market-indicators/{symbol}/investor-trading` | interval·count·until | 원 단위 매수/매도/순매수·기관 세부 → toss-index-investors:symbol:interval | 코스피/코스닥 시장 수급 |

인증은 `/oauth2/token` 한곳에서 공유한다. 스트림은 국내 `trade:kr`, `orderbook:kr`만 구독한다. 최대 2연결·연결당 100구독 계약 아래 현재 구현은 단일 연결에서 2유형 × 최대 50종목으로 제한하며, 그 밖의 조회는 묶음 REST로 처리한다. 실제 활성 종목 수와 계정 한도 내에서만 구독한다.

검증은 `test_toss.py`, `test_toss_market.py`, `test_toss_backfill.py`의 통화·시각·봉·단위·400/404 분리·401/429·캐시·커서·실패 보존 시험과 공개 화면 대조를 함께 사용한다. 전체 요청 조합이나 모든 종목의 확보 완료는 별도 감사 대상이다.

`TossClient`의 공유 토큰·요청 제한 → 원본 응답 → `TossCollector` 정규화 → `TossBackfill` 재개 커서 → DB → 공개 조회 API → Lightweight Charts·표로 연결합니다. OpenAlgo는 내부 조회 경로이며 인증을 중복 발급하지 않습니다.

가격 10년·거래 자료 1년에서 멈추던 종료 조건을 제거했습니다. 공급자가 마지막 페이지를 반환할 때까지 순환합니다. `전체` 버튼은 **현재 저장된 전체 기간**이며 모든 과거 자료의 수집 완료를 의미하지 않습니다. 화면을 연 종목의 과거 자료는 매 5회 중 1회 우선 수집하고, 나머지는 전체 종목 순환을 유지합니다.

회사 재무는 DART 원본·정규화 사실 테이블에 보존하고, 가격과 같은 토스 값으로 위장하지 않습니다. 사업연도 2015 이후 완료 보고서와 당해 분기 선택을 제공합니다. 누적 재무·분기·연결·별도·정정 이력을 구분합니다.

## 아직 증명되지 않은 것

- 모든 종목의 모든 페이지 수집 완료와 모든 날짜의 연속성
- 장중 실제 수신 후 강제 단절 복구의 최종 실증 (휴장 중 인증·구독 확인과 구별)
- EPS와 가격의 주식분할 기준 정합성 및 검증된 TTM EPS
- 배당 권리·지급일·기업행사를 포함한 전 기간 총수익률

실제 자료가 없는 선·봉·예측을 만들어 이 빈칸을 채우지 않습니다.
