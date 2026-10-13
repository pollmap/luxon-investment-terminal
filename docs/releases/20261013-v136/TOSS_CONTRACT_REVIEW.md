# 토스 공개 시장 조회 계약 검토

기준: **2026-10-13 20:50 KST에 내려받은 공식 명세**와 현재 저장소 코드. 인증·공급자 조회·DB·수집·새 WebSocket은 호출하지 않았습니다. 이 문서는 경로와 옵션의 정적 연결 검토이며 모든 옵션의 실제 성공 확인서가 아닙니다.

| 공식 공개 파일 | 버전 | 크기 | SHA-256 |
|---|---|---:|---|
| [REST 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json) | **1.2.24** | 465,774B | `7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40` |
| [AsyncAPI 명세](https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json) | **1.2.2** | 41,523B | `130251057fd9535a3e276099f9166b445f8c51f505f30540758e4b209231282e` |

공개 시장 GET은 25개이며 ETF 구성 조회를 제외한 **24개가 `PUBLIC_GET_PATHS`와 일치**합니다. 해외 종목·개인 계좌·주문은 제외합니다. US 장 일정은 기존의 보조 시장 정보로 유지하는 범위이며 해외 종목 화면을 뜻하지 않습니다. 명세는 작은 공개 파일을 각각 한 번 내려받아 해시를 계산했고, ignored `work/lucian-v136/toss-spec-review`에 보관했습니다.

## 경로 → 옵션 → 저장 → 화면

아래의 ‘연결’은 해당 함수·저장 키·공개 API·React 호출이 있다는 뜻입니다. 현재 원문 필드와 화면 숫자의 일치, 모든 조합의 실제 응답, 전 종목 완전 수집을 함께 완료로 표시하지 않습니다.

| 토스 GET 경로 (`/api/v1` 생략) | 코드에서 사용하는 옵션·커서 | 저장 및 화면 연결 | 남은 확인 |
|---|---|---|---|
| `/prices` | 국내 `symbols`, 최대 200종목 묶음 | 종목별 시세 → 목록·요약 | 전 종목의 시점·통합 범위 일치 |
| `/orderbook` | `symbol` | `depth` → 호가 | 실제 갱신·복구와 화면 반영 |
| `/trades` | `symbol`, 50건 | `depth` → 최근 체결 | REST 최근 50건과 스트림 공백을 전체 이력으로 표시하지 않음 |
| `/price-limits` | `symbol` | `toss-limits` → 가격 제한 | 현재 종목·날짜별 실제 값 |
| `/candles` | `1m/1d`, 200건, `adjusted`, `nextBefore` | `bars` → 선·캔들·OHLC·표 | 전체 종단·거래일·정렬 해시 |
| `/stocks` | 국내 `symbols`, 200개 묶음 | `toss-stock` → 종목 정보·정지 상태 | 개별 필드와 주식 종류 대조 |
| `/stocks/all` | 시장·상장 상태·주식 종류·보통주 여부 | `toss-listing` → 상장 현황 | UI는 KOSPI/KOSDAQ·STOCK 중심. 의도적인 범위 제한과 원천 누락을 구분 |
| `/stocks/{symbol}/warnings` | 국내 `symbol` | `toss-warnings` → 유의사항 | 제공 상태·정지·VI 등 실제 종류별 대조 |
| `/stocks/{symbol}/investor-trading` | 100건, `nextUntil` | 정규화 거래 이력 → 투자자 수급 | 명시적 종단과 누락 cursor 구분 |
| `/stocks/{symbol}/program-trades` | 동일 | 프로그램 수급 → 그래프·표 | KRX 기준·수량/금액·페이지 검증 |
| `/stocks/{symbol}/short-selling` | 동일 | 공매도 거래 → 기간 그래프·표 | 공매도 순보유잔고와 구분 |
| `/stocks/{symbol}/credit-trades` | 동일 | 신용 자료 → 기간 그래프·표 | 융자·대주 구분·페이지 검증 |
| `/stocks/{symbol}/securities-lending` | 동일 | 대차 자료 → 기간 그래프·표 | 거래와 잔고·페이지 검증 |
| `/exchange-rate` | 양방향 통화쌍·시각 | 선택별 캐시 → 금리·환율 | 시각 선택·최신 값 구분의 실제 응답 |
| `/market-calendar/KR` | 날짜 | 날짜별 달력·세션 → 거래 일정 | 휴장·특별 세션의 실제 원문 |
| `/market-calendar/US` | 현지 날짜 | 보조 달력 → 거래 일정 | 한국 시각 변환·현지 기준일 |
| `/rankings` | 6유형·7기간·유의종목 제외, 100건 | 선택별 순위 → 기간별 순위 | 모든 유효 조합의 실제 값; 등락 순위 realtime은 비활성 |
| `/market-indicators/prices` | KOSPI/KOSDAQ·국채 6종 | 지표 현재 값 → 시장·금리 | 지수 포인트와 금리 % 구분 |
| `/market-indicators/{symbol}/candles` | 지수 1m/1d·국채 1d, `nextBefore` | 지표 시계열 → 지수·금리 차트 | 종단 증거·일별 과거 범위 |
| `/market-indicators/{symbol}/investor-trading` | 4집계 간격·100건·`nextUntil` | 시장 수급 → 기관 세부·이전 자료 표 | 각 집계 간격의 실제 기간·단위 |
| `/sectors` | 목록 | 자체 산업 계층 → 분야별 찾기 | 계층·중복 소속 실제 대조 |
| `/sectors/rankings` | 2유형·6기간·KR | 선택별 산업 순위 → 산업 탭 | 기간별 실제 응답 |
| `/sectors/{sectorId}` | ID·KR | 설명·성과 → 산업 상세 | 설명과 기간 성과 실제 필드 |
| `/sectors/{sectorId}/stocks` | ID·KR | 구성 보통주 → 구성종목 | 하위 산업 중복·범위·관측 시점 |

새 공개 시장 경로의 누락은 발견하지 못했습니다. REST가 주·월봉을 별도 제공하는 것은 아니며 1m/1d 원자료의 서버 집계를 별도로 검증해야 합니다. 최대 건수를 고정해서 가져온 뒤 화면에서 더 보기로 나누는 처리는 provider `count` 입력창이 없다는 이유만으로 기능 누락으로 세지 않습니다.

`toss_market.py`와 `toss_sectors.py`의 옵션별 저장 키, `app.py`의 `/rankings /market-context /listings /indices/... /warnings`, `market-data.tsx /market-reference.tsx /toss-sectors.tsx`의 선택·조회 경로를 읽었습니다. 주식 목록·캔들·수급은 `toss.py`와 기존 상세 API를 경유합니다. 이번에는 브라우저에서 이 표의 모든 조작을 재실행하지 않았습니다.

## 실제 발견한 종단·실시간 공백

| 발견 지점 | 영향 | 이번 처리 |
|---|---|---|
| 기존 `audit_toss_coverage.py`는 `backfill_complete` 또는 가격 `ready + 커서 없음`으로 `last_page_received`를 계산 | 원문 없는 legacy와 누락 커서를 정상 종단처럼 셈 | **감사 도구 수정:** 명시적 null receipt·토스 출처·시간대가 있는 수신 시각·SHA-256 참조·종목/자료/시장/가격 조정 기준이 일치하고 남은 cursor/gap이 없을 때만 집계. legacy 상태는 변경하지 않고 `terminal_unverified`, 독립 검증 수는 null 유지 |
| 초기 검토의 `toss.py::_trading_page` 884/900은 `data.get(nextUntil)`과 `not cursor`를 사용 | 필드 누락·명시적 null의 의미 혼동 | 담당자에게 전달. 수집 본체의 수정·검사는 별도 기록으로 판정 |
| 초기 검토의 `toss_market.py::index_candles` 132/137도 동일 | 지수 과거 수집의 미확정 종료가 완료로 승격될 수 있음 | 담당자에게 전달 |
| 초기 검토의 `toss.py` WS 선언 1042~1047과 ACK 1059 | 거부 target을 기록하지만 다음 수요 변경에서 다시 선언할 수 있음 | 담당자에게 전달. target별 제외·정상 구독 보존이 필요 |

공식 거래 응답은 `records`, 지수 캔들 응답은 `candles`를 필수로 두지만 cursor 자체는 **optional string/null**입니다. 따라서 필드 누락을 곧바로 공식 스키마 위반으로 단정하거나 유효한 행을 모두 버려서는 안 됩니다. 이전 정상 cursor·값을 보존하고 미확정으로 남기는 것이 이번 완료 기준에 맞습니다. null 종료를 다루는 코드 존재와 원문 종단 증거 확보도 별개입니다.

AsyncAPI의 국내 체결·호가 연결은 full-replace 선언·최대 50종목×2유형·60초 PING·재연결·REST 스냅샷 복구·lossy 표시 경로가 있습니다. 명세에 없는 매수/매도 방향·누적 거래량·순서 번호를 만들어 표시하지 않습니다. 거부 target 처리 외에 지수 백오프의 jitter 권고와 과부하/강제 단절·브라우저 반영은 별도 검증 대상으로 남습니다. 계정 최대 2연결을 모두 사용하는 것은 완료 조건이 아닙니다.

## 재사용한 실제 증거와 한계

- `PRICE_COVERAGE.md/.json`: 2,761종목의 저장 메타 대조. 통합 수정·원주가 존재 각각 2,760종목, 원문 종단 receipt와 정렬 해시 marker는 0개. 실제 역사 행을 전수 재검증한 결과가 아닙니다.
- 기존 `stream-before.json`은 `both_kinds_observed=true`, `stream-after.json`은 false입니다. 두 기록의 epoch 복구·강제 단절·브라우저 반영·무손실 gate는 false입니다. 한 번의 서버 관측을 지속 수신과 사용자 화면 검증으로 바꾸지 않습니다.
- `test_toss_market.py`의 공식 예제 정규화와 `test_toss_history_page_contract.py`의 malformed-page 검사는 **fixture**입니다. 모든 계정·옵션·장중 호출의 실제 성공 증거가 아닙니다.
- 이번 수정한 감사 도구의 가벼운 합성 회귀 **38개 통과**. import 시 Store를 만들지 않으며 이 검사에서는 DB·공급자를 호출하지 않았습니다. SHA와 문서 ID를 함께 제공하면 동일한 64자리 해시여야 하며 다른 회사·자료 종류·시장·조정 기준 또는 문맥이 누락된 legacy 영수증은 거부합니다.

`metadata_terminal_receipt`와 `last_page_received`는 **논리적인 종단 영수증 메타데이터**입니다. 이번 검사에서 원문 파일을 열어 존재·내용을 재검증하거나 전체 시계열 행 수·정렬 해시를 검사하지 않았으므로 `independently_verified_complete_count`는 null입니다. 원문 참조 해시의 형식과 문맥 검증을 전체 과거 자료 검증으로 표시하지 않습니다.

명세 경로 연결은 24/24이지만, **실제 옵션·원문부터 화면 숫자까지의 검증 완료는 미확정**입니다. 재무·TTM·주주·배당 등은 이 시장 조회 명세에 전부 들어 있는 자료가 아니므로 DART·기업 자료의 검증을 그대로 유지합니다.
