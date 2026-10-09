# 최종 v93 Cloudflare 버전 일치

2026-10-09 02:16 KST. 기존 Worker `korea-stocks-edge`의 RELEASE만 `20261009-first-interaction-v93`으로 변경해 배포했다. Worker 코드·캐시 계약·계정·원점·요금제·호출 조건은 변경하지 않았다.

| 항목 | 확인 결과 |
|---|---|
| 배포 | 공식 Wrangler 4.148.0, 종료 코드 0 |
| Worker Version ID | `59861462-1484-4710-941d-a30471860532` |
| RELEASE | `20261009-first-interaction-v93` |
| 기존 Worker 주소 | `https://korea-stocks-edge.lch68-workers.workers.dev` |
| 공개 원점 | `https://stocks.62-171-141-206.sslip.io` |
| 원점 health | HTTP 200, `status=ok`, 같은 v93 release |
| 캐시 계약 | `public-read-v2`, 변경 없음 |
| Worker 소스 SHA-256 | `b578ff0e315f9ed4ccc4adae16573a6eb823b4923c88bf24f3b01d8bde17b626`, 변경 없음 |

앞서 클라이언트에서 차단된 Worker URL은 다시 호출하지 않았다. 이 기록은 공식 CLI 배포 메타데이터와 공개 원점 버전 일치 확인이며, Cloudflare 경유 화면·캐시 적중·성능 검증을 새로 통과했다는 의미는 아니다. 인증값은 출력하거나 문서에 저장하지 않았다.

근거: `work/operations-20261008/edge-deployment-v93-origin.json`.
