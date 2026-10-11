# 토스 API 구현·저장·화면·실제 응답 대조

코드 감사: 2026-10-09 01:38 KST, 당시 v91 작업 트리. 실제 API 응답 근거는 별도 시점인 v89의 01:02:37~01:19:33 KST 저장 관측이다. 이 두 시점을 같은 배포·옵션 검증으로 합치지 않는다. 이후 v92 반영과 공개 버전 확인은 문서 하단에 구분해 기록한다.

**공식 공개 조회는 25개, 우리 제품의 적용 대상은 24개다.** 섹터 관련 ETF 조회 1개는 최신 사용자 범위에서 제외한다. 현재 허용 목록 24개는 공식 명세와 일치하며, 24개 모두 기존 운영 세션에 실제 HTTP 200 응답 기록이 있다. 이 숫자는 전 종목·전 기간·모든 옵션·화면 검증 완료율이 아니다.

## 확인 범위와 증거

- 공식 명세를 인증 없이 다시 내려받아 `1.2.24`와 공개 조회 25개를 확인했다. [REST 명세](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
- 문서 SHA-256: `7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40`.
- 구현 기준: `services/market_dashboard/toss_contract.py`, `toss.py`, `toss_market.py`, `toss_sectors.py`, `app.py`와 현재 프런트 소스.
- 실제 응답 기준: `work/operations-20261008/backfill-metrics-audit.json`. 동일 세션 `c7111e066b7b4be4932fb1c5f16485aa`, 10월 9일 01:02:37~01:19:33 KST 관측. 인증 공급자를 추가 호출하지 않았다.
- 기존 20종 원본 해시·저장 상태: `docs/toss-single-live-v22.json`의 10월 3일 기록. 이 과거 파일의 종목 수나 자료 기간을 현재 확보율로 사용하지 않는다.
- 신규 섹터 공개 응답: `work/operations-20261008/context-public-v89/`와 `docs/releases/20261009-v89/context-public-audit.md`. 당시 공개 앱은 v89이며 섹터 169의 상세와 구성종목은 후속 조회에서 실제 `ready`를 확인했다.
- 명세·허용 목록·실제 응답 경로의 기계 대조: 같은 폴더 `toss-api-coverage-evidence.json`. 인증·계좌·개인정보는 포함하지 않는다.

아래의 ‘화면 연결’은 컴포넌트와 라우트가 이어져 있음을 코드로 확인했다는 의미다. 이번 감사에서 24개 기능의 PC·모바일 화면을 새로 조작해 검증한 것은 아니다. HTTP 횟수는 해당 세션 누계이며 새로 저장한 고유 행 수가 아니다.

## 공개 조회 25개 대조표

경로는 `/api/v1` 이하로 표기한다. 저장은 공통 원본 보관과 정규화/화면 스냅샷을 구분한다. 모든 대상 요청은 공통 `TossClient.get`의 원본 보관·최근 성공 기록을 통과한다.

| API | 구현·주요 옵션 | 정규화·저장 | 화면 연결 | 기존 실제 응답 |
|---|---|---|---|---:|
| `/prices` | `TossCollector.prices`, 최대 200개 묶음 | `quotes`, 시각·통화 검증 | 시장·검색·관심·상세 가격 | 200 × 180회 |
| `/orderbook` | `snapshot`, 단일 종목 | `depth:{code}`의 호가·잔량 | `depth-panel.tsx` 호가 | 200 × 109회 |
| `/trades` | `snapshot`, 최근 50개 | `depth:{code}` 체결 시각·가격·수량 | 같은 패널 최근 체결 | 200 × 109회 |
| `/price-limits` | `TossMarket.limits` | `toss-limits:{code}` 상·하한가 | `StockWarnings` 하단 | 200 × 71회 |
| `/candles` | `history`, 1분·일봉, 조정 여부, 이전 커서, 페이지당 200 | `market_time_series`와 `bars:*`, 최신·과거 커서 분리 | 선·영역·캔들·OHLC·기간·숫자 표 | 200 × 194회 |
| `/stocks` | `TossMarket.stocks`, 최대 200개 | `toss-stock:{code}` | 거래정지 등 종목 상태, 식별 보완 | 200 × 4회 |
| `/stocks/all` | `listings`, 시장·상장 상태·종류·보통주 여부 | `toss-listing:*`, 기존 대상과 대조 | `ListingTable`, 검색 대상 유지 | 200 × 3회 |
| `/stocks/{symbol}/warnings` | `warnings`, 실제 상세 요청으로 수집 | `toss-warnings:{code}`, TTL·실패 복구 | `StockWarnings` 유의사항·VI | 200 × 116회 |
| `/stocks/{symbol}/investor-trading` | `_trading_page`, 날짜 커서·100행 | `toss:investor-trading:*`, 수급 투영 | 개인·외국인·기관·기타, 매수/매도/순매수·표 | 200 × 87회 |
| `/stocks/{symbol}/program-trades` | 동일 수집 경로, 날짜 커서·100행 | `toss:program-trades:*`, `trading:*` | 차익·비차익 프로그램 흐름·일별 표 | 200 × 57회 |
| `/stocks/{symbol}/short-selling` | 동일 수집 경로, 날짜 커서·100행 | `toss:short-selling:*`, 공매도 거래 투영 | 거래량·금액·비율, 기간·일별 표 | 200 × 60회 |
| `/stocks/{symbol}/credit-trades` | 동일 수집 경로, 날짜 커서·100행 | `toss:credit-trades:*`, `financing:*` | 신용융자·대주, 신규·상환·잔고 | 200 × 55회 |
| `/stocks/{symbol}/securities-lending` | 동일 수집 경로, 날짜 커서·100행 | `toss:securities-lending:*`, `financing:*` | 대차 거래·잔고, 기간·표 | 200 × 75회 |
| `/exchange-rate` | `exchange_rate`, 통화 방향·기준 시각 | `toss-exchange-rate:*`, 현재/과거 키 분리 | 금리·환율, 시각·통화 선택 | 200 × 6회 |
| `/market-calendar/KR` | `calendar`, 기준일 | `toss-calendar:KR:*`, 거래 세션 | 국내 일정·다음 거래일 | 200 × 1회 |
| `/market-calendar/US` | `calendar`, 기준일 | `toss-calendar:US:*` | 해외 일정 참고만 제공 | 200 × 1회 |
| `/rankings` | `ranking`, 6종·기간·유의종목 제외·국내 | `toss-ranking:{종류}:{기간}:{제외}` | `MarketRankings`, 재정렬 버튼 | 200 × 36회 |
| `/market-indicators/prices` | `indicators`, 8개 지표 | `toss-market-indicators`, 지수/금리 단위 분리 | 코스피·코스닥, 국채 6종 | 200 × 6회 |
| `/market-indicators/{symbol}/candles` | `index_candles`, 지수 분·일봉/국채 일봉 | `toss-index:*`, 이전 커서 | 지수 차트·금리 추이 | 200 × 20회 |
| `/market-indicators/{symbol}/investor-trading` | `index_investors`, 일·주·월·년 | `toss-index-investors:*`, 원 단위 금액 | 시장 수급·기관 상세·표 | 200 × 2회 |
| `/sectors` | `collect_catalogue`, 계층·중복 검증 | `toss-sectors`, 300개 확인 | 공개 `/sectors/catalogue` 존재. 전체 계층 탐색 UI 미연결 | 200 × 1회 |
| `/sectors/rankings` | `collect_ranking`, 거래대금/상승률·6기간 | `toss-sector-ranking:*` | 산업 탭의 검색·순위·기간 | 200 × 6회 |
| `/sectors/{sectorId}` | `collect_detail`, 국내 | `toss-sector:{id}` | 산업 설명·6기간 성과 | 200 × 1회 |
| `/sectors/{sectorId}/stocks` | `collect_stocks`, 국내·대상 종목 대조 | `toss-sector-stocks:{id}` | 산업 구성 종목→상세, 169번 205개 확인 | 200 × 4회 |
| `/sectors/{sectorId}/etfs` | **제품 범위에서 제외** | 호출·저장 없음 | ETF 화면 없음 | 호출 안 함 |

원천 반환 행 수가 0인 메타데이터 API도 실제 200 응답이 있다. 현재 계측은 객체 형태의 상세·달력·섹터 목록을 모두 동일한 행 수로 세지 않으므로 `rows_received=0`만으로 빈 자료라고 판정하지 않는다. 예를 들어 섹터 목록은 실제 공개 응답에 300개가 있었다.

## 옵션·화면에서 남은 구체적인 차이

| 우선도 | 해당 API·코드 | 현재 차이 | 사용자 영향·완료 기준 |
|---|---|---|---|
| 높음 | `/sectors`, `toss-sectors.tsx` | 화면은 섹터 순위 결과 안에서만 검색하고 `catalogue`의 대·중·소 계층을 읽지 않음 | 순위에 없는 산업을 찾을 수 없다. 전체 목록·계층을 검색 범위에 연결하고 순위 없는 항목은 순위를 만들어 채우지 않아야 함 |
| 높음 | `/stocks/all`, `market-reference.tsx`, `market_selection.py`, 공개 `/listings` | `KR_ETC` 선택이 남아 있고 해당 목록을 현재 제품의 KOSPI/KOSDAQ 대상에 제한하지 않음. `openable=false`는 상세 진입만 막음 | 기타 국내시장 종목이 상장 현황에 보일 수 있어 최신 코넥스 제외 방침과 충돌. 자료 보존과 별개로 사용자 화면 범위를 제한해야 함 |
| 높음 | `/market-indicators/{symbol}/investor-trading`, `run_scheduled` | 일·주·월·년 조회는 가능하지만 자동 과거 페이지 순환은 `1d`만 대상으로 함 | 주·월·년은 첫 100개 뒤의 `next_until`이 있어도 계속 과거를 확보하는 경로가 없다. 해당 간격을 요청했을 때 커서가 끝날 때까지 재개하거나 실제 저장 범위를 표시해야 함 |
| 중간 | `/stocks/{symbol}/warnings`, `StockWarnings` | 서버는 오류·stale를 보존하지만 컴포넌트는 실패·pending을 표시하지 않고 빈 유의사항과 같은 빈 화면을 냄 | 조회 실패를 ‘유의사항 없음’으로 오해할 수 있다. 오류일 때만 짧은 상태와 재시도를 제공해야 함 |
| 검증·선택 미구현 | `/market-calendar/KR`, `/market-calendar/US`, `CalendarTable` | 국내는 `integrated`만 표시. 현재 저장된 실제 시간은 KR·US 모두 +09:00이며 문자열 HH:mm 표시가 한국시각과 일치함(아래 후속 확인) | 현재 오표시로 확정하지 않음. KRX/NXT 개별 선택은 미구현, DST 변경일·다른 오프셋 처리와 휴장 사례는 별도 검증 |
| 검증 | `/rankings`, `/exchange-rate`, `/stocks/all`, 섹터 순위 | 다양한 옵션을 코드로 받지만 이번 실제 증거는 옵션별 요청 조건을 기록하지 않는 경로 단위 집계 | 24개 200 기록만으로 6종×기간, 과거 환율·반대 통화, 상장 상태·주식 종류, 모든 섹터 조합을 실제 검증했다고 말할 수 없음 |
| 검증 | 캔들·종목 수급 5개·지수 캔들 | 커서 재개와 표·차트가 있어도 전 종목·전 기간 완료는 아님 | 미수집·404·자료 없음·원천 끝을 구분한 현재 확보율과 오류 종목 검사를 계속해야 함 |

새 메뉴를 늘리기보다 기존 산업 검색, 상장 현황, 시장 수급, 종목 상태 영역에서 보완하는 것이 적합하다. 이번 감사는 코드 수정 없이 남은 차이를 기록한다.

## 인증·실시간·토스 밖의 자료

- 인증은 공통 `TossClient.token`을 사용한다. 대상 24종의 성공 기록은 인증된 요청의 증거이며 별도 중복 인증을 실행하지 않았다.
- 국내 체결·호가 스트림은 `TossCollector.stream`, 공유 구독, 재연결 스냅샷 경로가 있다. 기존 구독 승인 기록은 실제 장중 새 이벤트·화면 반영·강제 단절 복구를 대신하지 않는다. 이번 감사에서도 장중 검증을 추가 완료하지 않았다.
- 개인 계좌·자산·주문·조건주문·매수 가능 금액과 ETF 조회는 대상에서 제외한다. API 수를 늘리기 위해 호출하지 않는다.
- 거래원·공매도 순보유잔고·재무·TTM·배당·주주·예상실적·뉴스는 이 토스 공개 API 24종의 완성도와 별도다. 기존 DART·기업 자료 원천의 확보·계산 검증이 필요하다.
- 상세 최근 체결은 실제 50개 REST와 수신 이벤트를 사용한다. 토스가 제공하지 않는 매수/매도 방향이나 누적 거래량을 생성하지 않는 제약을 유지한다.

## 판정

| 구분 | 이번 확인 결과 |
|---|---|
| 명세와 제품 범위 | 공식 25개 중 24개 적용, ETF 1개 제외 |
| 수집·저장 코드 | 적용 대상 24개 경로 존재 |
| 대표 실제 응답 | 기존 동일 세션에서 24개 모두 200 확인 |
| 화면 | 대부분 연결. 전체 섹터 계층, 목록 범위, 오류 상태 등 위 차이 남음 |
| 전 종목·전체 기간·옵션 | 완료로 판정하지 않음 |
| 실제 장중·복구 | 별도 실증 필요 |
| 코드 감사 당시 배포 | 01:38 KST의 작업 트리 확인이며, 표의 실제 응답은 v89 세션 근거 |

이번에 실행한 것은 공식 공개 명세 읽기, 기존 코드·응답 기록의 대조와 문서 생성이다. 토스 인증 조회·주문·계좌 호출, 공개 주소 변경, Worker 재배포는 수행하지 않았다.

## v92 후속 반영과 공개 확인

2026-10-09 01:58 KST 공개 원점 `/health`는 `status=ok`, `release=20261009-sector-history-v92`를 반환했다. 위 표의 24개 API 실제 응답 횟수는 이때 다시 수집한 수치가 아니라 앞서 명시한 v89 세션 관측을 유지한다.

- 전체 산업 catalogue를 검색·상하위 계층 탐색에 연결하고, 순위가 없는 산업에 가상의 순위를 만들지 않는다. 산업 상세·구성종목에서 탐색 상태로 돌아온다.
- 상장 현황의 `KR_ETC` 선택을 제거했다. 원본 자료와 내부 보관 범위를 삭제했다는 뜻은 아니다.
- 시장 투자자의 실제 요청된 주·월·년 과거 커서를 기존 호출 예산과 한 순환당 이전 페이지 1개 안에서 이어가도록 수정했다. 최신/과거 동시 저장을 직렬화하고, 최초 빈 응답·명시적 마지막 페이지·커서 누락·오류를 구분한다. 상세는 `../20261009-v92/market-investor-history.md`와 배포 후 관측을 참조한다.
- StockWarnings의 `error/stale` 재시도·마지막 경고 보존·10초 이상 pending 안내, 시장 수급의 `no_data` 문구를 추가했다. 정상적인 빈 유의사항은 장식 안내 없이 유지한다.

위 화면 변경은 프런트 담당의 회귀 검사·생산 빌드 결과와 배포 전달을 확인했다. 이 API 감사 작업이 모든 기기에서 해당 화면을 새로 조작한 증거는 아니다.

01:59 KST 기존 KR·US 달력 스냅샷을 각각 한 번 읽어 실제 시간 필드를 대조했다. 확인한 필드는 모두 `+09:00`이며 현재 화면의 `HH:mm`가 한국시각과 일치했다. **현재 시간대 오표시로 확정하지 않는다.** KRX/NXT 개별 세션 선택은 미구현이며, 미국 DST 변경일·다른 오프셋 입력의 변환은 미검증이다. `../20261009-v92/calendar-offset-audit.json`에 분리해 기록했다. 코드나 공급자 호출은 추가하지 않았다. 전체 종목·기간·옵션별 확보와 실제 장중 체결·복구도 24개 경로의 200 응답으로 완료 처리하지 않는다.

Cloudflare는 01:57 KST에 기존 Worker의 RELEASE를 v92로 맞췄다. 캐시 코드와 계약은 그대로이며, 앞서 클라이언트에서 차단된 Worker URL은 다시 호출하지 않았다. Worker 배포와 클라이언트 경유 성능 검증을 구분한다.
