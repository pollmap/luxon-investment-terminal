# v80 — 상세 요약에서 미사용 수집 상태 제거

## 변경

첫 종목 화면 UI가 읽지 않는 `collection` 객체를 summary 응답에서 제외하고, summary 경로에서 해당 저장값도 읽거나 응답 revision 의존성으로 확인하지 않게 했다. activity·fundamentals 등 다른 상세 뷰의 `collection` 상태는 유지한다. 웹 타입은 선택 필드로 바꿨다.

## 검증

- v79 전체 백엔드 회귀: **813 passed**. v80 수정 후 영향 영역 `tests/test_detail_response_cache.py`: **25 passed**.
- v80 후 `pnpm --filter @personal-fastgraphs/web typecheck`: 통과.
- 공개 health: 200, release `20261005-summary-no-collection-v80`, worker `collecting`, 유효 종목 2,766개.
- summary: 200, 1,887 UTF-8 bytes, `collection` 누락, 종목 코드·핵심 표시 정보 유지.
- fundamentals/activity: 각각 200, `collection` 포함.
- DB·Redis·web·OpenAlgo는 이 배포에서 교체하지 않았다.

## 100동시 공개 측정

PC에서 HTTPS HTTP/1.1 keep-alive, 100개 동시 연결, 같은 스크립트와 종목·검색어 표본을 사용했다. 값은 단일 실행 표본이다.

| 경로·호스트 | 최초 100요청 p95 | 300 warm 요청 p95 | 1.6초 후 100요청 p95 |
|---|---:|---:|---:|
| VPS 검색 | 3,468.08ms | 947.22ms | 1,316.26ms |
| VPS 종목 요약 | 581.96ms | 298.43ms | 599.99ms |
| Cloudflare Worker 검색 | 1,381.08ms | 678.26ms | 860.92ms |
| Cloudflare Worker 종목 요약 | 1,283.82ms | 634.45ms | 917.05ms |

상세 요약은 warm 표본에서 500ms 목표를 통과했지만 최초·1.6초 후에는 초과했다. 검색 목표 300ms도 통과하지 못했다. 따라서 시스템 전체의 성능 목표 달성으로 판정하지 않는다. Cloudflare 시험 경로는 이 표본에서 기존 VPS 공개 경로보다 느렸다.

이전 요약 본문 관측 약 44,988 bytes와 비교하면 v80 응답은 1,887 bytes다. 약 95.8% 감소했지만 이는 응답 본문 크기 변화이며 전체 체감속도 개선률은 아니다.

측정 원본: `edge-v78-20261005.json` (로컬 보존 자료: `../../edge-v78-20261005.json`), `edge-v79-20261005.json` (로컬 보존 자료: `../../edge-v79-20261005.json`), `edge-v80-20261005.json` (로컬 보존 자료: `../../edge-v80-20261005.json`).

## 남은 항목

전 종목 과거 자료·404 보완, TTM과 주식분할 기준 확장, 배당 정보 원문 대조, 실제 장중 수신 및 재연결 검증, 검색·요약 부하 목표의 안정적 통과, PC 실사용 평가가 남아 있다.
