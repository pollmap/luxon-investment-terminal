# 정적 화면 배포

`apps/web/out`을 Cloudflare Static Assets와 기존 VPS에 같은 바이트로 배포합니다.
토스·DART 수집, DB, API와 WebSocket은 VPS에서 실행합니다. Next.js standalone 빌드는 복구용으로 유지합니다.

v98부터 Cloudflare 화면의 공개 조회·CSV·WebSocket은 기존 `stocks.62-171-141-206.sslip.io`로 직접 연결합니다. 추가 WAN 경유와 Worker 동적 요청을 줄이기 위한 선택입니다. `public-transport.ts`와 서버 `public_transport.py`가 정확한 두 공개 Origin만 사용하며 인증정보를 보내지 않습니다. Worker의 API 경로는 기존 링크 호환용입니다. 정적 자산 캐시와 API 캐시는 다른 경로이므로 같은 성능 개선이라고 합산하지 않습니다.

v105의 `public-read-v3-origin` 계약은 Origin별 캐시 키로 `Vary: Origin` 응답을 분리합니다. Origin 없는 요청과 문자열 `null`은 다른 키입니다. 응답의 CORS 헤더와 Vary는 보존하고, 다른 Vary·private·인증정보·쿠키는 캐시하지 않습니다. 실제 Worker 캐시 적중을 확인했지만 1초를 넘겨 가격 응답을 재사용하지 않습니다. 이 Worker 변경만으로 직접 VPS 조회를 사용하는 UI 전체가 빨라졌다고 보고하지 않습니다.

## 빌드와 검사

PowerShell, 저장소 루트:

```powershell
$env:MARKET_RELEASE = '배포 버전'
$env:NEXT_PUBLIC_SITE_URL = 'https://stocks.62-171-141-206.sslip.io'
pnpm.cmd --filter @personal-fastgraphs/web build:static
node scripts/check-market-static.mjs
node --test ops/cloudflare/worker.test.mjs
```

빌드 작업은 `work/build/static-web`에 앱의 필요한 소스만 복사하고 Next 정적 export를 수행합니다. 서버 API routes와 회사별 동적 리다이렉트는 복사하지 않습니다. 완료된 `out`만 교체하며 실패하면 이전 산출물을 유지합니다. 임시 소스·컴파일 결과는 성공/실패 후 삭제합니다. 원본 코드, `.env`, standalone 산출물은 이동하거나 삭제하지 않습니다. 중복 빌드를 막는 `static-web.lock`이 남으면 실행 중 빌드가 없는지 확인한 다음 해당 빈 디렉터리만 정리합니다.

`build-manifest.json`에는 릴리스·생성 시각·파일별 SHA-256이 있습니다. `MARKET_RELEASE`와 Worker의 `RELEASE`를 동일하게 맞춥니다. 이 값은 배포 식별자이며 자료의 기준 시각을 대체하지 않습니다.

## Cloudflare

기존 격리 Wrangler를 사용합니다. 새 패키지 설치는 필요 없습니다.

```powershell
node work/tools/cloudflare-v89/node_modules/wrangler/bin/wrangler.js deploy --config ops/cloudflare/wrangler.jsonc --dry-run --outdir work/cloudflare-static-dryrun
# 검증 후 배포 담당자가 동일 RELEASE로 실행
node work/tools/cloudflare-v89/node_modules/wrangler/bin/wrangler.js deploy --config ops/cloudflare/wrangler.jsonc --var RELEASE:$env:MARKET_RELEASE
```

- 정적 파일은 자산 라우터에서 제공하므로 보통 Worker를 실행하지 않습니다.
- API·이전 링크·내부 경로 차단만 Worker-first입니다. `/stocks?code=...`의 검색 조건은 브라우저에서 읽습니다.
- API는 HTML fallback을 사용하지 않습니다. 내부·계좌·인증 경로는 공개하지 않습니다.
- `screener`, `screener/export`, `analysis/scenario`의 POST 본문과 WebSocket handshake를 보존합니다. 주문 POST를 허용하지 않습니다.
- 현재가 최대 1초·확정 재무 최대 5분의 기존 신선도 계약, 원천 Age/Date, private/no-store/오류 처리와 ETag 재검증을 유지합니다.
- 새 주소로 자동 이동하지 않습니다. 브라우저별 관심종목·설정은 기존 백업·가져오기 기능으로 이전합니다.

## 기존 VPS

`ops/market/promote_static_web.py`를 사용합니다. `apps/web/out` 내용 자체를 tar.gz로 만들고 서비스 `incoming`에 업로드한 후 로컬·원격 SHA-256을 대조합니다. 서버에서 먼저 dry-run을 실행하고 같은 인자로 승격합니다.

```text
python3 /srv/services/korea-stocks/incoming/promote_static_web.py --expected-release <active-release> --archive /srv/services/korea-stocks/incoming/<static-archive>.tar.gz --sha256 <verified-sha256> --dry-run
```

dry-run의 release·manifest·Nginx 구조·컨테이너 소유 확인 후 `--dry-run`만 제거해 실행합니다. helper는 `current`가 바뀌면 중단하고 기존 배포 잠금을 공유합니다. 아카이브 경로·멤버·파일별 해시·릴리스를 검증한 뒤 `releases/<version>/static-web`에 저장합니다. `/srv`의 비공개 디렉터리 권한을 넓히지 않고 검증된 정적 파일만 `/var/www/korea-stocks/releases/`에 하드링크(파일시스템이 다르면 복사)로 게시합니다.

`nginx-static-locations.conf`는 이 공개 트리의 위치 규칙 참고본입니다. helper는 해당 stocks HTTPS server의 기존 웹 location만 교체하고 API·WebSocket upstream, TLS, 캐시, 다른 서비스의 server 블록을 보존합니다. 기존 Nginx 설정을 백업하고 `nginx -t`·reload·loopback/public HTML/JS 해시·manifest 검증 뒤 web 컨테이너를 stop만 합니다. 재검증 통과 후 `static-web-deployment.json`에 active·release·공개 위치·검증 시각을 기록합니다.

승격 실패 시 이전 공개 포인터·정적 디렉터리·Nginx 설정·실행 중이던 웹 컨테이너를 복구합니다. 실패 후보와 이전 정적 폴더는 조사·안전한 정리를 위해 남기며 자동 삭제하지 않습니다. 이 helper는 DB·수집·인증·인증서·다른 서비스를 변경하지 않습니다.

Nginx graceful reload 직후에는 구 worker의 정상 200 응답이 잠시 돌아올 수 있습니다. 승격 검사는 최대 15초 동안 재시도하되 loopback/public HTML·JS 해시와 manifest가 한 번의 전체 검사에서 모두 일치해야 통과합니다. 시간 초과 시 경로·경유 방식·상태·크기·해시·캐시 관련 헤더만 기록하고 복구하며, 응답 본문은 로그에 남기지 않습니다. 첫 불일치와 최종 검증 결과는 성공 marker에 보관합니다.

기존 `/company/{code}/{view}`, `/terminal`, `/screener`, `/portfolio`, `/system`은 정적 호환 페이지가 `legacy-routes.mjs`를 사용해 종목·기간·필터를 새 주소로 옮깁니다. Cloudflare는 같은 함수로 HTTP 302를 반환합니다. 잘못된 회사코드와 API 오류를 정상 종목으로 바꾸지 않습니다.

## 공개 반영 조건

정적 직접 진입·스크립트·폰트·이미지·PWA, 검색·CSV POST·WS, 이전 링크, 키보드·관심 저장·복귀를 같은 릴리스에서 확인합니다. 새 주소의 403/차단이 남으면 이를 우회하거나 성공으로 취급하지 않고 기존 주소를 유지합니다. 검증 전 Next 웹 컨테이너를 종료하지 않습니다. 백엔드 상태·자료 확보·장중 수신·100동시 성능은 정적 빌드 통과와 별도 기록합니다.

공식 근거: [Next 정적 export](https://nextjs.org/docs/app/guides/static-exports), [Cloudflare 선택적 Worker-first](https://developers.cloudflare.com/workers/static-assets/routing/worker-script/).
