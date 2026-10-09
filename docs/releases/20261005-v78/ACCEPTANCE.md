# v78 — 배당 총수익 비교 API 제거

## 변경

사용자가 제외한 배당 재투자·현금배당 총수익 비교 경로 `GET /api/market/v1/securities/{code}/analysis/performance`를 공개 API에서 제거했다. 배당금·배당수익률·지급 이력 기능은 유지했다. 과거 원주가가 필요한 가격 차트는 `/bars`에서 계속 요청한다.

## 로컬 검증

- 전체 테스트: `uv run --extra market --extra dev python -m pytest` — **812 passed**, 3,871개 deprecation warning, 37.57초.
- 제거된 종목별 경로가 404이고 OpenAPI에 등록되지 않았는지 확인했다.
- `/bars?interval=day&adjustment=raw&venue=통합` 요청이 계속 raw-history를 요청하는 회귀 검사와 quote metrics 검사를 실행했다 — 5 passed.
- 정적 분석 도구는 이 배포 판정의 통과 기준으로 실행하지 않았다.

## 배포와 공개 확인

2026-10-05 KST에 백엔드 전용 배포 도구로 `20261005-excluded-dividend-total-return-v78`을 공개했다. 배포 스크립트는 API·worker만 교체하고, DB·Redis·web·OpenAlgo 컨테이너 ID가 바뀌지 않았는지 확인했다.

| 공개 경로 | 결과 |
|---|---|
| `/api/market/v1/health` | 200, release `20261005-excluded-dividend-total-return-v78` |
| `/api/market/v1/search?q=005930` | 200, 삼성전자 검색 확인 |
| `/api/market/v1/securities/005930?view=summary` | 200, 종목 코드 일치 |
| `/api/market/v1/securities/005930/analysis/performance` | 404 |
| `/api/market/v1/securities/005930/bars?interval=day&adjustment=raw&venue=통합` | 200 |
| `/stocks?code=005930` | 200 |

공개 상태 조회에서 API·worker 이미지가 v78, web은 v76, OpenAlgo는 v73이었다. PostgreSQL·Redis의 컨테이너 ID도 배포 전과 같았다. API·worker 외 서비스를 재시작하지 않았다.

## 남은 검증

이 배포는 전 종목 과거 자료, TTM·주식분할 기준, 배당 정보 원문 대조, 실제 장중 수신·복구, 인터넷 경유 부하, UI 실사용을 완료하지 않는다. 전체 완료 여부는 이들 별도 기준으로 판정한다.
