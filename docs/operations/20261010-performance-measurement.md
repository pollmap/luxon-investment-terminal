# 공개 성능 표본 재분류 — v106 과거 측정

2026-10-10에 기존 표본만 재집계했습니다. 새 네트워크 요청·부하 시험·서버 변경은 없습니다.

- 측정 시각: 2026-10-09T18:46:26.714Z (2026-10-10 03:46 KST).
- API: `20261010-selective-rows-v106`; 정적 화면: `20261010-chart-projection-v102`. 릴리스는 당시 ACCEPTANCE 기록에서 연결했으며 원시 JSON 자체에는 릴리스 필드가 없습니다.
- 원시 파일: `work/api-miss-v106/public-100-http2.json` (658,433 B), SHA-256 `95f0e3dad100145d7234172e6d5548eab4e935a04aac655c3c3da0bf89248b41`. 원본은 변경하지 않았습니다.
- HTTP/2 TLS 연결 하나에서 동시 스트림 100개, 전체 2,000응답. 기존 요약 132개 필드가 모두 동일합니다.

## 실제 캐시 상태별 반복 단계

`warm`은 첫 배치 뒤의 반복 요청입니다. 캐시 HIT를 뜻하지 않습니다. 아래 통계는 해당 단계의 원시 표본을 실제 헤더로 다시 나눈 값입니다.

| API 경로 | 기능 | Edge | 원점 헤더 | 표본 | 응답 p95(ms) | TTFB p95(ms) | 현재 app 표본 / p95(ms) |
|---|---|---|---|---:|---:|---:|---:|
| VPS 직접 | search | ORIGIN | EXPIRED | 300 | 1662.51 | 1159.37 | 300 / 805.37 |
| VPS 직접 | summary | ORIGIN | EXPIRED | 200 | 798.74 | 795.23 | 200 / 334.76 |
| VPS 직접 | summary | ORIGIN | HIT | 100 | 1511.7 | 1511.45 | 0 / — |
| Worker API | search | HIT | EXPIRED | 111 | 252.9 | 250.19 | 0 / — |
| Worker API | search | HIT | HIT | 10 | 262.06 | 261.52 | 0 / — |
| Worker API | search | MISS | EXPIRED | 142 | 1003.55 | 1003.45 | 142 / 267.02 |
| Worker API | search | MISS | HIT | 37 | 662.03 | 661.96 | 0 / — |
| Worker API | summary | HIT | EXPIRED | 80 | 207.48 | 207.36 | 0 / — |
| Worker API | summary | HIT | HIT | 120 | 355.25 | 355.22 | 0 / — |
| Worker API | summary | MISS | EXPIRED | 95 | 724.64 | 724.62 | 95 / 194.74 |
| Worker API | summary | MISS | HIT | 5 | 724.69 | 724.66 | 0 / — |

Edge HIT에 포함된 원점 헤더는 캐시가 생성될 때의 기록입니다. 현재 원점에 새 요청이 갔다는 뜻이 아닙니다. 해당 HIT의 app 시간은 현재 처리 시간에서 제외했습니다.

## 해석과 남은 측정

- 전체 반복 단계 p95는 VPS 검색 1,662.51ms·요약 869.74ms, Worker API 검색 999.34ms·요약 724.19ms입니다. 모두 당시 API 표본이며 현재 v116 성능이나 화면 사용 속도로 대체할 수 없습니다.
- 최초 배치도 MISS만 있는 것은 아닙니다. 1.6초 뒤 배치 역시 실행 시점이며 실제 EXPIRED 여부는 원점 헤더로 구분합니다. JSON은 최초·반복·1.6초 후 각각의 Edge·원점 헤더·두 헤더 조합 통계를 담습니다.
- 실제 Cloudflare 화면은 VPS API로 직접 연결합니다. 검색 색인 로딩 뒤 입력은 브라우저에서 처리하며, 상세 재조회는 If-None-Match를 사용합니다. 기존 HTTP2 검사는 Origin·조건부 요청·브라우저 동작을 재현하지 않았습니다.
- 과거 2,000개 표본에는 요청 경로가 없습니다. 배열 순서에서 종목·검색어를 추정해 채우지 않았습니다. 수정한 측정기는 성공·실패 표본 모두 request_path를 기록합니다.
- TTFB에는 프록시·대기·전송이 포함되고 app은 현재 앱 응답 헤더 생성까지입니다. 서로 다른 통계의 p95를 빼서 순수 네트워크 지연을 만들지 않았습니다. 연결 시작 비용도 개별 요청 시간에 포함되지 않습니다.
- 새 실제 화면 과제와 현재 릴리스 부하 검증은 별도 실행 항목입니다. 이번 재집계로 목표 통과를 새로 판정하지 않습니다.

## 변경과 순수 검증

- scripts/benchmark_market_http2.mjs: 기존 CLI와 first/warm/after_1600ms·samples 필드 유지, 요청 경로·측정 계약·실제 캐시 분류 추가. 분류 함수를 import해도 네트워크나 파일 생성이 없습니다.
- scripts/benchmark_market_http2.test.mjs: 신규 8개 회귀. 기존 market_benchmark_metrics.test.mjs의 7개와 합계 15개 통과; node --check 통과.
- 기존 원시 파일의 SHA와 모든 기존 요약 필드 동등성을 재집계 실행 중 다시 검사했습니다.
