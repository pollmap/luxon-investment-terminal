# v79 — 종목 요약 응답 경량화

## 변경

첫 종목 화면의 요약 API에서 상세 재무 기간표와 원천 추적 원문을 제외했다. 요약은 가격·등락·시장 상태·거래량·거래대금·시가총액·주요 지표와 해당 값의 기간·단위, 회사 설명만 제공한다. 재무 기간 전체는 기존 지연 조회 경로에서 그대로 제공한다. 저장된 `detail-summary` 스냅샷에서도 요약 화면에 사용하지 않는 재무 배열을 제외한다.

## 로컬 검증

- 전체 백엔드 테스트: **813 passed**, 3,871 warnings, 41.11초.
- 투영 변경 후 `tests/test_detail_response_cache.py`: **25 passed**.
- 검색·요약에 필요한 필드와 값, 원천 추적 제거, 상세 재무 보존, 응답 10KB 미만을 회귀 검사했다.

## 배포와 공개 확인

2026-10-05 KST에 `20261005-compact-stock-summary-v79`를 백엔드 전용 배포 도구로 공개했다. 배포 결과 API·worker만 갱신됐다. 공개 상태는 web v76, OpenAlgo v73, PostgreSQL·Redis unchanged다. 공개 health는 release v79, worker `collecting`, 현재 범위 2,766종목을 반환했다.

| 공개 확인 | 결과 |
|---|---|
| `/api/market/v1/health` | 200, v79, worker collecting |
| `/api/market/v1/securities/005930?view=summary` | 200, 3,496 UTF-8 bytes, 축약 필드 유지 |
| `/api/market/v1/securities/005930/financials/timeline` | 200, 33행 |
| `/api/market/v1/securities/005930?view=fundamentals` | 200, 상세 재무 응답 유지 |
| `/api/market/v1/securities/005930/bars?range=252&interval=day` | 200, 3,000행 |
| `/api/market/v1/securities/005930/analysis/performance` | 404, 범위 제외 경로 유지 |

요약 API는 가격과 거래일 `2026-10-02 19:59 KST`를 구분해 반환했다. 받은 시각이 갱신됐다고 거래일을 최신으로 바꾸지 않는다.

## 공개 100동시 표본

같은 PC에서 HTTPS HTTP/1.1 keep-alive, 최대 100개 동시 연결, 같은 스크립트·검색어·대표 종목으로 실행했다. 이 측정은 단일 표본이며 보편적인 용량 보증은 아니다.

| 경로 | v78 warm p95 | v79 warm p95 | v79 1.6초 후 p95 | 판정 |
|---|---:|---:|---:|---|
| VPS 검색 | 548.58ms | 363.72ms | 629.83ms | warm 구간은 300ms 초과 |
| VPS 종목 요약 | 1,732.97ms | 567.00ms | 521.41ms | 500ms 목표 미달 |
| Cloudflare Worker 요약 | 992.90ms | 673.08ms | 675.02ms | 목표 미달, VPS warm보다 느림 |

요약 JSON 본문은 앞선 관측 약 44,988 bytes에서 v79의 3,496 bytes로 줄었다. VPS warm p95는 개선됐지만 상세 목표를 통과하지 못했다. Cloudflare Worker의 별도 시험 주소도 현재 측정으로는 기존 공개 도메인의 대체안이 아니다.

단건 공개 요약에서 `Server-Timing: app`은 3.38~14.62ms였다. 따라서 남은 부하 지연을 앱 핸들러 시간만으로 설명할 수 없다. 전송·TLS·프록시·revision 조회 비용의 부하 구간별 계측이 남는다.

측정 원본: `edge-v78-20261005.json` (로컬 보존 자료: `../../edge-v78-20261005.json`), `edge-v79-20261005.json` (로컬 보존 자료: `../../edge-v79-20261005.json`).

## 남은 완료 조건

전 종목 과거 자료·404 보완, TTM과 주식분할 기준의 전 종목 확장, 배당 정보 원문 대조, 실제 장중 체결·호가 수신 및 복구, 부하 목표 통과, PC 실사용 검증은 완료하지 않았다. worker가 실행 중이라는 사실만으로 백필이나 원천 자료가 완료됐다고 보지 않는다.
