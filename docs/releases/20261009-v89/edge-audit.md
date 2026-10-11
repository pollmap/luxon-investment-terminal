# Cloudflare 조회 캐시 점검

2026-10-09 KST. 이 기록은 엣지 코드 수정·로컬 검사·기존 Worker 배포·소량 공개 상태 확인 결과입니다. **100동시 성능 통과 기록은 아닙니다.**

## 발견한 문제와 수정

| 기존 동작 | 변경 |
|---|---|
| 캐시 읽기 오류가 전체 요청 오류로 이어질 수 있음 | 원점 조회로 복구. 저장 실패도 정상 응답을 막지 않음 |
| `private`, `Vary: *` 등을 검사하지 않고 공유 TTL로 덮음 | private·Set-Cookie·미지원 Vary는 공유 저장 제외. 안정 자료의 no-store/no-cache도 존중 |
| 안정 자료가 원점 캐시에 머문 시간을 다시 5분 TTL로 처리 | 원점 Age·Date를 남은 엣지 수명에 포함 |
| API HIT마다 브라우저에 새 max-age=300 전달 | API는 브라우저 재검증, Worker 내부에서 남은 수명 동안 응답. ETag가 같으면 304 |
| 일부 HTTP 오류의 원점 캐시 지시가 그대로 전달됨 | 404·429·500 등 오류는 no-store. Worker 하위 fetch의 별도 캐시도 사용하지 않음 |
| HTTP 200 재무 응답을 준비 상태와 무관하게 5분 저장 | 원점의 `X-Market-Cache-State: confirmed`가 있어야 재무 공유 캐시 허용 |
| 예전 캐시 계약의 엔트리를 새 정책으로 재사용 가능 | 캐시 키에 `public-read-v2` 추가. `X-Market-Edge-Contract`로 확인 |

시세 포함 응답은 기존 `X-Market-Validated-At`부터 최대 1초 조건을 유지합니다. 과거 자료 기준 시각은 응답 본문을 그대로 보존하며 엣지에서 최신 시각으로 바꾸지 않습니다. 호가·체결·장중 차트·WebSocket·인증·쿠키·내부 진단은 새 캐시 대상으로 늘리지 않았습니다. 명시적인 새로고침과 Range 요청도 공유 캐시를 우회합니다.

현재 API는 재무 `confirmed` 헤더를 제공하지 않으므로 해당 응답은 일반 조회로 전달합니다. 이 헤더는 HTTP 200 여부가 아니라 검증된 실제 기간·입력 상태를 판정한 다음 추가해야 합니다. 수집 대기나 기간 근거 부족을 캐시해 연결 작업을 가리지 않기 위한 결정입니다.

Cloudflare의 Cache API는 응답의 캐시 지시·ETag를 사용하며 일부 헤더와 상태에 저장 제약이 있습니다. 캐시 조회는 자체적으로 원점을 다시 조회하지 않으므로 실패 복구를 Worker에서 처리합니다. [공식 Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/). 하위 요청의 `cache: 'no-store'`는 공식 지원 옵션입니다. [공식 Request API](https://developers.cloudflare.com/workers/runtime-apis/request/).

## 검사

```powershell
node --check ops/cloudflare/worker.mjs
node --test ops/cloudflare/worker.test.mjs
```

문법 검사와 **12개 테스트**를 통과했습니다. 만료된 시세·원점 Age·private·Vary·Set-Cookie·HTTP 오류·준비되지 않은 재무·캐시 장애·ETag·사용자 강제 재검증·내부 경로 차단을 검사했습니다. 모의 런타임 검사이며 실제 Cloudflare POP 캐시 동작을 검증한 것으로 표시하지 않습니다.

## 공개 경로와 인증 관측

- 기존 시험 주소: `https://korea-stocks-edge.lch68-workers.workers.dev`
- 00:23 KST의 소량 순차 요청에서 Python urllib 클라이언트는 HTTP 403, Cloudflare error 1010을 받았습니다. 브라우저 전체 장애라고 단정하지 않습니다. 해당 제어를 우회하지 않았습니다.
- 처음 `wrangler whoami`는 undici/esbuild 모듈 해석 오류로 종료했습니다. `work/tools/cloudflare-v89`에 **같은 Wrangler 4.148.0을 격리 설치**해 복구했습니다. 앱·전역 node_modules는 변경하지 않았습니다.
- 기존 OAuth를 정상 갱신한 뒤 `whoami` 인증에 성공했습니다. 새 키 발급·재로그인 요청 없이 해결했으며 값·계정 정보는 출력하지 않았습니다.
- 기존 `korea-stocks-edge`에 새 계약을 배포했습니다. 최종 Worker 버전은 `d843d710-60d8-4dd7-b19d-f439a66e287d`이며 원점 RELEASE는 실제 health에서 확인한 `20261005-dart-index-continuation-v88`입니다.
- 기존 정상 Worker 버전 `93deb527-933b-4d58-9a88-4d2128b51043`을 되돌리기 대상으로 기록했습니다. 근거는 `work/operations-20261008/edge-deployments-before.json`과 `edge-deployment-v89-final.json`입니다.
- 배포 후 00:40 KST에 health를 1회 확인했으나 동일 Python 클라이언트는 CF 1010/403을 받았습니다. Worker 계약 헤더가 없어 Worker 실행 앞에서 거절되는 것으로 추정합니다. 이어 인앱 브라우저도 같은 health 주소에서 `net::ERR_BLOCKED_BY_CLIENT`를 반환했습니다. 현재 환경에서 공개 Worker 동작 검증이 차단된 상태로 기록하며 반복 호출·우회는 하지 않았습니다.
- SSH 변경, 기본 공개 주소 이동, 100동시 부하 시험은 실행하지 않았습니다. 기존 앱의 관심종목·설정·공유 주소도 변경하지 않았습니다. 새 계약의 공개 HTTP HIT·304·오류 헤더는 실제 허용된 접속에서 추가 확인해야 합니다.

격리 CLI 실행 경로:

```powershell
node work/tools/cloudflare-v89/node_modules/wrangler/bin/wrangler.js whoami
node work/tools/cloudflare-v89/node_modules/wrangler/bin/wrangler.js deploy --config ops/cloudflare/wrangler.jsonc --keep-vars
```

## 성능 판정

과거 `docs/edge-v80-20261005.json`은 Windows PC의 HTTP/1.1 100동시 요청 결과입니다. 원점 반복 구간은 검색 p95 947.22ms·상세 298.43ms, Worker 반복 구간은 검색 678.26ms·상세 634.45ms였고 캐시 HIT/MISS가 섞여 있습니다. 그 수치를 현재 v89 또는 순수 HIT의 성능으로 사용하지 않습니다.

이번 수정은 캐시의 정확성·장애 격리·재검증 전송량을 개선합니다. **검색 300ms·상세 500ms 달성이나 몇 배 개선을 주장하지 않습니다.** 서버 용량 복구와 동일 버전 반영 후, 다른 압축·부하 시험과 겹치지 않는 조건에서 원점/엣지·적중/미적중·만료를 분리해 측정해야 합니다. 캐시 1초 예산을 늘리거나 공개 주소를 옮겨 수치만 맞추지 않습니다.

로컬 `ops/cloudflare/wrangler.jsonc`의 오래된 v56 RELEASE는 실제 확인한 v88로 맞췄습니다. 원점이 새 v89 앱으로 전환된 뒤 RELEASE를 다시 맞추고, 새 계약 헤더·404 no-store·정상 조회를 확인해야 합니다. 캐시의 릴리스 변수와 Worker 배포 ID, 원점 앱 버전은 서로 다른 값입니다.

## 원점 v89 전환 후 변수 갱신

2026-10-09 01:05 KST 전후에 기존 공개 health에서 `20261009-recovery-jusikdam-v89`를 확인했습니다. Worker의 RELEASE도 정확히 같은 값으로 변경해 기존 Worker에 배포했습니다.

- 최종 Worker 버전: `ec220557-98d5-4d42-a475-42062db49c6e`.
- 코드 SHA256: `b578ff0e315f9ed4ccc4adae16573a6eb823b4923c88bf24f3b01d8bde17b626`. 앞선 배포와 동일하며 RELEASE만 변경했습니다.
- 기존 원점·Worker 주소, OAuth, `public-read-v2` 캐시 계약은 유지했습니다.
- 근거: `work/operations-20261008/edge-deployment-v89-origin.json`.
- 차단된 workers.dev 경로는 다시 호출하지 않았습니다. 기존 sslip.io 공개 API의 새 기능 검사와 Worker POP 검증을 구분합니다.
