# 현재 국내 주식 앱의 CI

2026-10-10 기준입니다. 이 검사는 코드와 fixture의 회귀를 확인합니다. 실제 공급자 수신, 전 종목 확보율, 공개 성능, 배포 완료를 대신하지 않습니다.

## 자동 검사

`.github/workflows/market.yml`은 PR, `master`·`codex/public-stock-dashboard` push와 수동 실행에서 동작합니다.

| 단계 | 범위 |
|---|---|
| Python | `tests/test_*.py`의 현재 앱 회귀. 하위 디렉터리의 과거 터미널 검사는 제외 |
| 외부 연결 제외 | `network` 또는 `integration` 표시가 있는 검사는 실행하지 않음 |
| Node | 시장 차트·요청 제어, Cloudflare Worker, 성능 측정값 해석의 순수 함수 검사 |
| 타입 | `next typegen`으로 기본 생성 경로를 만든 뒤 `tsc --noEmit` |
| 정적 웹 | 격리 빌드 디렉터리에서 candidate 생성, manifest·파일 해시·경로 검사 |
| 브라우저 | `first-chart.spec.ts`, `request-efficiency.spec.ts`. API·WebSocket을 fixture로 대체 |

패키지와 Chromium 설치에는 네트워크를 사용합니다. 검사·타입 생성·빌드·브라우저 실행은 Linux 네트워크 namespace 안에서 loopback만 켜고 일반 runner 계정으로 실행합니다. 공급자·운영 VPS·외부 PostgreSQL로 나가는 경로가 없습니다. 배포 키나 API 키를 workflow에 전달하지 않습니다.

Python 실행기는 별도로 기존 공급자·DB 환경변수를 제거하고 임시 SQLite 저장 위치를 사용합니다. 외부 소켓과 PostgreSQL·Redis 기본 포트 및 로컬 복원 포트 54420을 차단합니다. 이 Python 가드는 로컬 오작동 방지용이며 하위 프로세스까지 격리하는 운영체제 sandbox는 아닙니다. CI에서는 namespace가 전체 프로세스 트리를 격리합니다.

정적 검증 서버는 `/stocks`를 `stocks.html`로 연결합니다. `/api`, `/internal`, `/ops`의 요청을 외부 서비스로 전달하지 않으며, 경로 탈출과 디렉터리 목록도 허용하지 않습니다. 브라우저 검사에서 실제 데이터가 필요해지면 해당 fixture를 보완해야 합니다.

## 로컬 실행

의존성을 설치한 Python 환경과 Node.js 24, pnpm 10.29.3을 사용합니다. Windows에서는 아래 `python`을 `.venv/Scripts/python.exe`로 바꿀 수 있습니다.

```sh
python -m pip install ".[dev,market]"
pnpm install --frozen-lockfile
python scripts/run_market_tests.py --collect-only
python scripts/run_market_tests.py --junitxml work/ci/python.xml
# 필요한 루트 파일만 선택할 수 있습니다.
python scripts/run_market_tests.py --test test_market_ci_runner.py

node --test apps/web/tests/market/*.node.test.mjs apps/web/tests/market/search-proxy.test.mjs
node --test ops/cloudflare/worker.test.mjs scripts/market_benchmark_metrics.test.mjs
pnpm --dir apps/web exec next typegen
pnpm --dir apps/web typecheck
node scripts/build-market-static.mjs
node scripts/check-market-static.mjs
```

정적 빌드는 로컬 `apps/web/out`을 candidate로 교체합니다. 공개 서버로 배포하지 않습니다. 공개 배포본 검증에는 해당 release의 저장된 산출물을 사용해야 합니다.

브라우저 검사를 실행할 때는 별도 터미널에서 `python scripts/run_market_tests.py --serve-static --port 3219`를 실행합니다. 검사 터미널에서 `MARKET_WEB_URL=http://127.0.0.1:3219`를 설정한 뒤 다음을 실행합니다.

```sh
pnpm --dir apps/web exec playwright test --config playwright.market.config.ts first-chart.spec.ts request-efficiency.spec.ts --reporter=list --output=../../work/ci/browser-results
```

로컬 검사는 CI의 운영체제 네트워크 격리와 같지 않습니다. 위 두 브라우저 파일은 전체 시장 API와 WebSocket을 fixture로 처리합니다. 실제 API를 먼저 조회하는 다른 브라우저 파일을 이 게이트에 추가하지 않습니다.

## 과거 검사와 남은 별도 게이트

`.github/workflows/ci.yml`의 과거 터미널 전체 Python·웹 검사는 `Legacy terminal (manual)`로 보존했습니다. 자동 PR 게이트로 사용하지 않습니다. 기존 수집용 수동 workflow도 변경하지 않았으며 현재 앱 CI는 이를 실행하지 않습니다.

현재 앱 CI가 통과해도 실제 장중 수신·전 종목 데이터·운영 PostgreSQL·백업 복원·공개 인터넷 성능·사용자 평가·배포는 별도 검증이 필요합니다. 외부 연결 검사를 생략한 결과를 실제 연동 성공으로 기록하지 않습니다.
