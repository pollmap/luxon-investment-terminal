# v92 Cloudflare 버전 일치

2026-10-09 01:57 KST에 기존 Worker `korea-stocks-edge`의 RELEASE만 `20261009-sector-history-v92`로 변경해 배포했다. 공식 Wrangler 4.148.0의 격리 설치본을 사용했고, 인증정보·계정값은 기록하지 않았다.

| 항목 | 결과 |
|---|---|
| Worker | `korea-stocks-edge` |
| 기존 검증 주소 | `https://korea-stocks-edge.lch68-workers.workers.dev` |
| 원점 | `https://stocks.62-171-141-206.sslip.io` 유지 |
| RELEASE | `20261009-sector-history-v92` |
| 새 Version ID | `6f6145ae-dd5b-48de-a850-a44673a0cc80` |
| 캐시 계약 | `public-read-v2`, 변경 없음 |
| Worker 코드 SHA-256 | `b578ff0e315f9ed4ccc4adae16573a6eb823b4923c88bf24f3b01d8bde17b626`, 변경 없음 |
| 공개 원점 health | 01:58 KST, HTTP 200·status ok·같은 v92 release |

배포 CLI는 성공 코드 0, 업로드·트리거 배포·버전 ID를 반환했다. 기존 클라이언트 차단을 우회하거나 Worker URL 검증을 반복하지 않았다. 따라서 이번 기록은 배포 메타데이터와 원점 버전 일치의 증거이며 Cloudflare 경유 화면·POP 캐시·속도 목표 통과의 증거는 아니다. 기존 공개 주소, 요금제, 호출 한도는 변경하지 않았다.

근거: `work/operations-20261008/edge-deployment-v92-origin.json`, `work/operations-20261008/market-investors-v92-public-request.json`.
