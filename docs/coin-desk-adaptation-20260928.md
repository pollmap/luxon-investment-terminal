# Coin Desk의 검증 가능한 장점을 국내증시에 적용

참조 작업: 기획 코인 분석 플랫폼 (로컬 보존 자료: `codex://threads/01a0b9fd-9c01-7b62-9477-4f09c0f6d97c`).
참조 checkout: `btc-dashboard`, 버전 0.17.1, HEAD `93b93d0647344b3b1557c43aaa20947f534e73c4`.
해당 작업의 실행 중인 자동화와 배포는 변경하지 않는다. 코인 데이터·개인 자료·키를 국내증시로 복사하지 않는다.

## 확인한 기능과 적용 결정

| 참조 코드·문서 | 좋은 점 | 국내증시에 적용 |
|---|---|---|
| `shared/analysis-workspace.ts`, RELEASE15 | 낙폭·월별 수익률을 가격과 함께 읽기 | 실제 국내 종가로 계산하는 ‘가격 자세히 보기’. 휴장일을 결측으로 판정하지 않으며 관측 범위를 명시 |
| `shared/chart-readings.ts`, RELEASE16 | 시간 선택과 표·차트의 동일한 데이터 | 하나의 날짜 선택으로 가격·수익률·낙폭을 함께 확인. 이전·다음·최근, 표·CSV 제공 |
| `src/MetricGuide.tsx`, RELEASE17 | 설명에서 실제 차트로 적용 | ‘얼마나 변했나요 / 고점에서 얼마나 내려왔나요 / 어느 달에 변했나요’ 큰 선택 버튼과 짧은 뜻 보기 |
| `src/query-cache.ts`, `src/hooks.ts` | 중복 요청 병합·늦게 도착한 다른 자산 응답 격리 | 기존 종목별 React key·AbortController·revision cache 유지. 새 패널도 같은 종목·기간의 응답만 표시하고 숨긴 화면 조회 중단 |
| `worker/storage.ts` | 바뀌지 않은 스냅샷의 DB 쓰기 생략 | SQLite/PostgreSQL 원자적 조건부 upsert. 동일 payload는 revision·Redis 알림·응답 캐시를 유지 |
| `scripts/backup_check.py` | 테이블을 자동 발견하는 백업 복원 검사 | 국내 SQLite의 모든 사용자 테이블·뷰·인덱스·트리거·BLOB을 자동 검사. 별도 복원 파일에서 스키마·내용·무결성 대조 |
| `worker/backfill-budget.ts`, RELEASE17 | 수집 예산·개별 자산 실패 격리 | 기존 국내 수집기의 공급자별 제한·종목 체크포인트를 유지. 코인용 1만 관측/일·분 단위 주기를 국내 원천에 복사하지 않음 |
| `shared/automation-observation.ts` | 최근 정상 자료와 실패·복구를 별도 판정 | 기존 수집 status/last_success/last_attempt와 장애 검증에 활용. 장외 시간에 시세 미수신을 장애로 판정하지 않음 |
| `shared/relative-analysis.ts` | 같은 날짜의 수익률 비교, 미래 값 참조 금지 | 기존 종목 비교를 재사용. UTC 24/7 연속 일자 조건은 그대로 이식하지 않음 |
| RELEASE16의 로컬 자료함·개인 메모·드로잉 | 개인 자료를 서버 시세와 분리 | 기존 관심·설정의 브라우저 저장 유지. 새 개인 자료 업로드 기능은 이번 조회 흐름에 추가하지 않음 |
| RELEASE17 캔들 패턴·코인 파생/온체인 | 실제 확정 입력이 있을 때만 신호 | 국내 OHLC·계정 실시간 확보 전에는 패턴을 만들지 않음. 펀딩·청산·온체인 지표는 국내 주식에 대응하지 않음 |

참조 모바일 화면 `docs/audit-15/btc-390.png`를 확인했다. 촘촘한 코인 도구의 작은 컨트롤은 그대로 가져오지 않는다. FAST Graphs [Historical Graph 공식 설명](https://docs.fastgraphs.com/en/articles/9419962-historical-graph)의 기간 선택·가격 성과 확인도 함께 참고하며, 국내증시는 터치 날짜 선택 아래에 큰 숫자를 고정 배치한다.

## 구현한 화면·데이터 계약

- 경로: `/stocks?code=005930&tab=요약` 또는 차트 탭의 ‘가격 자세히 보기’.
- 목적: 종목 가격의 기간 변화·고점 대비 하락·월별 변화를 작은 표 조작 없이 읽기.
- 첫 화면은 기존 가격 차트를 유지하고 새 패널은 접는다. 공유 링크로 지정된 분석은 바로 연다.
- 선택 우선순위: 같은 종목의 명시적 링크 → 종목별 저장값 → 1년·가격 변화·최근 관측.
- `insight`, `insight_years`, `insight_date`는 새 선택값이며 기존 링크와 관심종목을 보존한다.
- 새 조회는 저장된 가격 스냅샷만 사용한다. 외부 API를 화면 요청마다 호출하지 않는다.
- 수익률은 종가 관측값의 변화이며 배당·재투자 성과와 구분한다. 시장·조정 기준을 혼합하지 않는다.
- 낙폭은 선택 구간 시작 이후의 관측 최고 종가 대비다. 전체 상장 기간의 최대 낙폭으로 표현하지 않는다.
- 월별 변화는 직전 달 마지막 관측에서 해당 달 마지막 관측까지다. 직전 달 자료가 없으면 null. 달력 검증 없이 월말 자료 확보를 확정하지 않는다.
- 로딩·수집 없음·재시도·마지막 정상 데이터·출처 및 계산 방법을 구분한다. 다른 종목이나 기간의 숫자를 임시 표시하지 않는다.
- 모든 선택은 키보드 가능, 날짜는 이전/다음/최근 버튼 제공, 뜻 보기 닫은 뒤 초점 복귀.
- 최소 360·390·430px, 큰 글씨, 다크에서 가로 넘침 없이 검증한다.

## 검증 기록

검증일은 2026-09-28 KST다. 아래 결과는 `.next-coindesk-v22` 웹(3241)과 로컬 API(8241), 기존 SQLite 자료를 사용했다. 원본 Coin Desk 작업의 서버·자동화·코드는 변경하지 않았다.

### 실제 반영한 변경

| 변경 | 파일 | 결과 |
|---|---|---|
| 관측 종가 기반 기간 수익률·선택 구간 고점 대비·월별 변화 | `services/market_dashboard/price_context.py`, `app.py` | 새로운 `/api/market/v1/securities/{code}/analysis/price-context`와 revision/ETag 캐시 |
| 가격 자세히 보기와 뜻 보기 → 차트 적용 | `apps/web/components/market/price-context-panel.tsx`, `detail.tsx` | 큰 선택 버튼, 날짜 이동, 월별 격자, 표·CSV·공유 링크, 종목별 선택 저장 |
| 기존 탐색과 공유 선택 보존 | `apps/web/components/market/dashboard.tsx`, `apps/web/app/stocks/market.css` | 기존 URL·관심 설정 유지, 확대 글씨에서 단어 단위 버튼 줄바꿈 |
| 동일 스냅샷의 불필요한 쓰기·무효화 생략 | `services/market_dashboard/store.py` | 조건부 원자적 upsert; 동일 값은 DB revision·Redis 알림·캐시를 바꾸지 않음 |
| 모든 테이블의 별도 백업 복원 | `scripts/verify_market_backup.py` | 읽기 스냅샷을 고정한 SQLite online backup, 별도 복원, 스키마·타입 포함 행 해시·무결성 검사 |
| 전 종목 계산 감사와 부하 측정 확장 | `scripts/audit_price_context.py`, `scripts/market_load_test.mjs` | 원천 요청 없이 전체 저장 자료 검사; 기존 부하 도구에 선택적 분석 경로 추가 |

기존 수집 예산·체크포인트·재시도·구독 공유는 유지했다. 코인용 주기나 지표를 국내 주식에 복사하지 않았다. 자체 점수·매매 기능·코인 데이터·개인 메모 업로드를 추가하지 않았다.

### 자동 검사와 실제 저장 자료

- 공개 시장 관련 회귀 검사 **173개 통과**. 새 계산/동시 저장/백업 검사는 18개이며 전체 173개에 포함된다. 새 Python 파일 Ruff, 변경 TSX ESLint, TypeScript, Next.js production build 통과.
- 등록 **2,872개 종목**의 저장된 가격으로 1년 분석을 실행해 `ready=2872`, 계산 예외 0건이었다. 이는 **저장 자료의 계산 가능 여부**다. 최신 가격, 원천 전체 기간, 분할 조정 기준의 검증을 뜻하지 않는다. 전체 계산 감사 (로컬 보존 자료: `price-context-all-securities-20260928.json`)
- 삼성전자·삼성전자우·SK하이닉스·코넥스 238540의 실제 로컬 API 응답과 ETag 304를 확인했다. 대표 응답과 별도 Python 부하 기록 (로컬 보존 자료: `coin-desk-adaptation-runtime-20260928.json`)
- **2,495,799,296바이트 SQLite**를 별도 파일로 백업·복원했다. 5개 사용자 테이블의 스키마·행 내용, 무결성·외래키 검사가 일치했고 107.411초가 걸렸다. 운영 DB를 교체하지 않았다. 결과는 `storage/public-market/backups/coin-desk-adaptation-20260928-verified/verification.json`이다.
- 초기 백업 검사는 제한된 임시 저장 경로 때문에 실패했다. 대용량 행 대신 작업 디렉터리에 정렬된 32바이트 해시 청크를 보관하도록 수정한 후 위 결과를 얻었다. 실패 기록과 성공 기록은 분리되어 있다.
- 동일 값 저장·변경 값 저장·NaN 입력 거부·서로 다른 SQLite 연결 4개의 동시 최초 저장을 검사했다. PostgreSQL 분기는 구현되어 있으나 실제 PostgreSQL 인스턴스에서는 실행하지 않았다.

### 브라우저에서 확인한 동작

Chrome의 같은 출처 iframe으로 360·390·430·768·1440px를 확인했다. 데스크톱 스크롤바 때문에 실제 내용 너비는 각각 341·371·411·749·1421px다. 물리적 Android 기기나 200% 브라우저 확대 검사를 대체하지 않는다.

- 링크의 `monthly / 3년 / 2026-02-27` 선택 복원과 숫자 일치.
- 이전 날짜를 누르면 실제 직전 관측일 2026-02-26으로 바뀜.
- 뜻 보기에서 가격 변화를 선택하면 창이 닫히고 해당 차트로 이동하며 기간·날짜 유지.
- 더 크게 글씨에서 5개 너비 모두 가로 넘침 0건. 새 패널의 주요 버튼 높이 52px 이상.
- 라이트 360px와 시스템 다크 화면을 직접 확인. 가격·수익률 부호와 선택 테두리 유지.
- 처음 발견된 360px 확대 글씨의 단어 중간 줄바꿈을 수정한 v22에서 다시 확인.
- 브라우저 관측 기록 (로컬 보존 자료: `coin-desk-browser-20260928.json`). CSV 저장과 클립보드 복사 버튼은 구현했지만, 내려받은 파일 및 OS 클립보드까지 읽는 검사는 이번에 수행하지 않았다.

### 성능과 미달 항목

기존 Node HTTP 측정 도구로 각 경로 300회, 동시 연결 100개, warm cache, gzip 응답 전체를 읽었다. 브라우저 렌더링 시간이나 클라우드 성능이 아니다.

| 경로 | API 직접 p95 | 웹서버 경유 p95 | 기준 |
|---|---:|---:|---|
| 검색 | 158.88ms | **420.56ms** | 웹 경유 300ms 목표 미달 |
| 상세 요약 | 120.75ms | 248.66ms | 로컬 500ms 목표 이내 |
| 기존 전체 상세 | 155.93ms | 230.78ms | 호환 경로 유지 |
| 새 가격 분석 | 179.98ms | 293.71ms | 새 조회 경로 |

HTTP 오류는 두 측정 모두 0건이다. API 결과 (로컬 보존 자료: `coin-desk-load-api-node-20260928.json`), 웹 경유 결과 (로컬 보존 자료: `coin-desk-load-web-node-20260928.json`).

별도의 Python/httpx 동시 부하에서는 웹 경유 p95가 검색 3795.28ms·요약 3947ms·가격 분석 4466.51ms로 더 느렸다. 이전 실행본과 직접 API에도 클라이언트 동시성에 따른 지연이 재현되었다. 분리 진단 (로컬 보존 자료: `coin-desk-proxy-diagnosis-20260928.json`). 두 측정 방법을 같은 수치로 합치거나 빠른 결과만 전체 성능이라고 제시하지 않는다. 전후 동일 조건의 10배 향상은 확인하지 않았다.

### 실행 및 확인

현재 실행 화면: <http://127.0.0.1:3241/stocks?code=005930&tab=%EC%9A%94%EC%95%BD&insight=drawdown&insight_years=3>.

가격 차트 아래의 ‘가격 자세히 보기’를 열면 세 분석을 바꿀 수 있다. 위 링크는 새 패널을 펼치고 고점 대비 화면을 연다. 공개 서버 배포는 이번 작업에 포함된 검증 결과가 아니다.

재실행은 저장소 루트에서 다음과 같이 한다. 이미 실행 중인 포트를 중복 시작하지 않는다.

```powershell
./scripts/start-market.ps1 -SkipWorker -WebPort 3241 -ApiPort 8241 -BuildDir .next-coindesk-v22
.venv/Scripts/python.exe -m pytest tests/test_price_context.py tests/test_snapshot_noop.py tests/test_market_backup.py -q -p no:cacheprovider
.venv/Scripts/python.exe scripts/audit_price_context.py --output docs/price-context-rerun.json
```

`SkipWorker`는 개발용 검증 서버가 기존 수집 작업을 중복 실행하지 않도록 한 것이다. 신규 증권사 인증·실제 장중 수신, 검증된 TTM 입력, 공매도·대차·신용 원천 보완, 공개 배포, 실제 Android·아버님 사용 평가는 별도 미완료 항목으로 남아 있다. 이번 가격 분석은 기존 저장된 종가를 사용하며 이 외부 의존성을 완료로 바꾸지 않는다.

### 참조 소스 고정값

- `shared/analysis-workspace.ts`: `2498111AB94B859740059C1D0A6F290FF21BE2F1B9872DB84C8CC39E8C15D1A2`
- `worker/storage.ts`: `340700D351F2871B649ACF1B18038EE9A1BB997BF9390087D80281376AA262B5`
- `scripts/backup_check.py`: `CE89D3D258E185388EDF870D98E77B1429A1C83E5B372331CD0312663BB8B91B`

SHA256은 이번에 읽은 파일의 값이다. 참조 작업은 계속 개발 중이므로 이후 HEAD와 현재 화면이 달라질 수 있다.
