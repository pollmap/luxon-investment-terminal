# 주식담 설계 결정

현재 제품 범위와 실행 위치는 [README](README.md), 검증 상태는 [v130 기록](docs/releases/20261011-v130/ACCEPTANCE.md)을 기준으로 합니다. 아래의 날짜별 항목은 이전 LUXON 연구 터미널부터 이어진 결정 이력이며, 충돌하는 범위·배포 방식은 최신 사용자 결정과 최신 항목을 우선합니다.

## 2026-10-11 캔들 작업 화면·검색·운영 중단 기준

최초 차트는 달력 기준 3개월·일봉·캔들입니다. 명시적 링크와 기존 저장 선택을 우선하며 기본값 복원은 별도 행동입니다. PC에서는 목록과 호가를 차트 양옆에 배치하고, 휴대폰에서는 차트 조작을 기간·봉·모양 선택창 한 줄로 줄입니다. 종목 제목은 차트를 덮지 않으며 요약·차트·심화 자료의 실패와 로딩을 분리합니다.

최근 세 회계연도의 누락은 이전 연도로 채우지 않습니다. 회계기간 계약이 없는 응답은 현재 연도를 임의 추정하지 않고 확인된 마지막 회계연도를 표시합니다. 주가 확보 기간과 서버가 검증한 실적 비교 가능 구간을 별도로 표시합니다. 브라우저는 서버의 비교 불가 판정을 되돌리지 않습니다.

종목 검색은 즉시, 문서 검색은 사용자의 Enter·검색 버튼으로 실행합니다. 공개 GET에서 DDL·문서 수집·모델 다운로드를 하지 않습니다. 운영의 제목 fallback은 제목 검색으로 표시하고 본문·의미 검색 검증과 분리합니다. 문서 버전·해시·절·표 머리글·회사 근거를 보존하며 AI 답변 생성은 추가하지 않습니다. PostgreSQL의 기능 검사와 자료 조회는 같은 제한된 읽기 연결에서 수행하고 요청 제한을 유지합니다.

공유 VPS의 정상 DB·원본·다른 서비스는 보존합니다. 파일 단위 외부 복원 검증이 끝난 비활성 배포본만 정리했습니다. 독립 DB의 전체 행·정렬 해시 검증은 통과하지 않았으므로 원본 이전·운영 저장 전환을 하지 않습니다. 최신 계획의 90% 중단·80% 미만 및 쓰기 검사 후 재개를 적용하고, 색인·대량 수집을 재개 조건 충족으로 오인하지 않습니다.

성능은 100 독립 TLS 연결과 20 독립 검색 연결의 실제 결과로 판정합니다. 서버·프록시·인터넷·캐시 조건을 분리하며 빠른 일부 응답만 목표 달성으로 보고하지 않습니다. 두 차례 수정·측정 후에도 미달이면 기록하고 중단합니다. 이번 조건에서 Cloudflare 동적 API가 더 느렸으므로 정적 전달에만 사용합니다.

## 2026-10-10 배당 날짜의 독립 재조합과 실패 백업 정리

공시 문서 파싱의 완료와 이후 날짜 근거의 반영은 별도 버전으로 관리합니다. 날짜 관측만 바뀌면 저장된 배당 행과 대조하고 원문·공급자 상태·수신 시각은 보존합니다. 새 확정 지급일을 확보했다는 상태와 기존 근거의 투영 오류 수정을 구분합니다.

정기 백업의 실패 partial은 이름이나 나이만으로 삭제하지 않습니다. 48시간 이상 갱신이 없고 실제 검증된 복구 덤프보다 이전인 파일만 대상으로 하며, 동일 백업 잠금·열린 파일·단일 링크·inode/mtime/크기와 복구본의 실제 SHA-256을 검사합니다. 완료 덤프·배포 전 백업·원본은 제외합니다. 실패 파일 정리는 용량 사전검사 전에 실행해 오래된 실패 쓰기가 다음 백업을 영구적으로 막는 경우를 줄이며, 안전 조건을 확인하지 못하면 해당 파일은 보존합니다.

성능 후보는 응답 동일성과 지연 결과를 함께 봅니다. v108의 키별 잠금 후보는 인위적 충돌을 해소했지만 실제 로컬 요약 지연 회귀를 배제하지 못해 제외했습니다. 좋은 결과만 고르기 위한 반복 시험이나 공개 성능 향상 주장은 하지 않습니다.

## 2026-10-10 재무 입력 상태와 요청별 계측

계산된 연간/TTM 사실과 부분 기간 입력을 분리해 저장합니다. 부분 입력 상태가 비교 가능 여부를 승격하지 않습니다. 원문 재처리는 명시적인 로컬 경로로 한정하고 원본 수신 시각·해시·회사·공개일과 개별 작업 재시도 제한을 보존합니다. 공급자 backoff 중에도 로컬 작업은 진행할 수 있지만 네트워크 fallback은 하지 않습니다.

공개 성능은 앱과 전달 계층을 분리해 측정합니다. 요청별 계측은 고정 단계·횟수만 반환하고, 캐시 HIT의 과거 작업 시간과 공유 작업 참여자의 미측정 시간은 새 작업 또는 0ms로 취급하지 않습니다. 계측 추가를 속도 목표 달성으로 보고하지 않습니다.

## 2026-10-10 수급 원천 분리와 Origin별 캐시

네이버 integration의 일별 수급은 `naver:investor-trading:{code}`에 별도 저장합니다. 거래시장 범위가 다른 토스 통합 수급의 값·상태·참조·관련 facts를 덮지 않습니다. Toss 자료가 없거나 기존 자료가 검증 가능한 순수 Naver 이력인 경우에만 원천을 명시한 fallback으로 제공합니다. 이미 혼합된 legacy 자료는 검증·보존 없는 재분류나 삭제를 하지 않습니다. 네트워크 대기 중 Toss가 갱신한 보호 필드는 마지막 DB 저장 트랜잭션에서 다시 읽습니다.

Cloudflare의 `Vary: Origin` 공개 응답은 실제 요청 Origin을 URL 캐시 키와 Request 헤더에 함께 반영해 분리합니다. Origin 없음과 문자열 `null`도 구분합니다. CORS 허용 범위를 넓히거나 Vary를 제거하지 않으며 가격의 원래 검증 시각부터 1초 이내라는 조건은 유지합니다. 쿠키·인증·다른 Vary·오류는 공유하지 않습니다. 정적 화면의 직접 VPS API 경로와 Worker 호환 API 경로의 성능은 별도로 보고합니다.

## 2026-09-28 public dashboard integration

The active Korean dashboard preserves anonymous use and existing links. Continuous valuation comparisons require attested periods, publication time, share class and split basis. Annual/TTM and actual/assumption remain separate. A selected-period trimmed multiple is retrospective, never a point-in-time strategy input. Actual stored prices can appear without an earnings comparison when its inputs are unverified.

Raw binary archives and append-only financial facts coexist with snapshots. Receipt IDs are not publication timestamps; Decimal values and XBRL dimensions survive normalization. Existing DART archives restored 23,747 dated facts for 2,586 securities, but calculated TTM remains zero. Broker connections, actual market receipt, physical Android use and public deployment are independent gates.

Official FAST Graphs public screens informed the primary graph, optional comparison controls and adjacent reading panel. On phones the reading panel is below the graph, 52px controls accompany touch selection, corporate detail is collapsed, and larger text applies to the bottom navigation. Responsive Chrome inspection found and fixed 10px navigation text and truncated metric controls. See `docs/integration-implementation-20260928.md` for measurements and unfinished implementation, including weighted per-share inputs and live validation of supplemental KIS/KRX adapters.

KRX basic/daily adapters keep raw-price history separate from the primary broker. KIS schedules retain record and payment dates; incomplete corporate actions cannot unlock total returns. Opaque estimate cells remain raw observations until units and row meanings are verified. Search responses now coalesce concurrent builds by source revision.

The local runner verifies the built Next rewrite destination before starting processes; changing only the runtime API variable does not change a built rewrite. The final web proxy and reconnect check is reproducible with `scripts/verify_market_runtime.py`. Issued shares and major shareholders use disclosures, with phone quantities displayed in readable rows instead of three narrow columns.

Status: active architecture and product decisions
As of: 2026-08-16

This document records the current public implementation contract. Historical
experiments, private design identifiers, local paths, authenticated reference
captures, and superseded product names are intentionally excluded from the
public snapshot.

## Product boundary

- The product and canonical repository are named **LUXON Investment Terminal**
  and `pollmap/luxon-investment-terminal`.
- LUXON is a personal investment-research workspace, not an automated trading
  or investment-advice system.
- The beta operating target is a single-user, KR-first terminal. US and JP
  provider boundaries remain prepared but are not described as production-ready
  without source-backed evidence.
- Public source code does not imply a public hosted service. Local Windows plus
  Docker Compose is the default beta profile; Vercel, Render, and managed
  Postgres are optional protected profiles.

## Public repository and reference policy

- The public repository is a sanitized current snapshot. Full historical work
  remains in a separate private archive.
- FAST Graphs-style historical valuation workflow is the primary interaction
  reference. FnGuide-style Snapshot, Financials, Consensus, and Peers structure
  is the secondary information-architecture reference.
- Reference work is limited to manual review of public documentation and public
  pages. Authenticated automation, scraping, access-control bypass, protected
  DOM extraction, and repository storage of third-party screenshots are
  prohibited.
- LUXON uses its own brand, copy, code, components, tokens, data model,
  deterministic formulas, and source-trace model. It may reproduce analytical
  tasks and domain-standard chart grammar, not third-party trade dress or
  proprietary assets.
- The active frontend contract is
  `docs/CLAUDE_DESIGN_HANDOFF.md`. Its blocking validation companion is
  `docs/CLAUDE_DESIGN_QA_CHECKLIST.md`. Former Figma work is private archival
  input, not a public source of truth.
- The repository intentionally grants no open-source license. Public visibility
  is for source inspection and portfolio review; reuse requires separate
  permission.

## Financial-data integrity

- LLMs do not create, rank, repair, or silently fill financial numbers.
- Production values come from first-party filings, verified APIs,
  operator-supplied validated files, user-entered assumptions, or deterministic
  formulas.
- Raw payloads are append-only. Restatements are versioned rather than
  overwritten.
- Every stored value requires `source_trace`; point-in-time values require
  `source_trace.available_at`; derived values require `formula` and
  `input_fact_ids`.
- A missing value is not zero. Missing credentials, source contracts, upstream
  failures, stale values, and rate limits remain distinct statuses.
- Fixtures stay labeled `fixture_non_production` and cannot become research,
  training, production, or operational evidence.
- Production fixture fallback is fail-closed unless the operator explicitly
  enables it for a non-production environment.

## KR-first data plan

- The first production slice is the configured KR priority universe, with
  source-backed coverage from OpenDART and validated market-data inputs.
- Priority-universe order is not displayed as a live market-cap rank until
  source-backed price, shares, or market-cap rows are recomputed for the same
  as-of policy.
- The KR valuation-map may use a DB-free source-backed cache only when every row
  passes provenance and non-fixture validation. Otherwise it returns a
  source-required state.
- Consensus and peer data are fail-closed. FnGuide/DataGuide endpoints,
  credentials, schemas, and values are never guessed. Licensed operator exports
  must pass an explicit import contract before persistence.
- Consensus snapshots are point-in-time records. Analyst scorecards may compare
  estimates with actuals only where valid historical snapshots overlap.

## API and frontend contract

- Research responses use a shared envelope with `available`, `status`,
  `data_mode`, nullable `data`, and `source_trace`.
- Supported states include `ready`, `partial`, `stale`, `configured`,
  `fixture_non_production`, `missing_source`, `missing_contract`, `missing_key`,
  `rate_limited`, and `upstream_error`.
- A frontend may render financial values only when the envelope and trace state
  permit it. Unavailable states must not expose stale fixture numbers behind a
  positive badge.
- Historical Graph is the primary decision surface. Every displayed financial
  number, chart point, valuation line, and table cell must lead to Fact Audit.
- Company routes preserve ticker and workspace deep links. Redirect-only route
  wrappers are transitional and are a blocking item for the Claude Design pass.
- Workspace requests must have independent loading and error boundaries; one
  endpoint failure must not force an unrelated screen into a global fallback.
- File-backed chart runs accept canonical UUIDs only. File-backed chart layouts
  accept canonical UUIDs and validated lowercase owner slugs only. Chart-cache
  folders accept canonical market-ticker segments only; every resolved path
  must remain inside its configured storage root.
- A `raw:<source>:<sha256>` evidence identifier resolves only when the source
  slug is safe, the digest is exactly 64 hexadecimal characters, and the
  selected file's computed SHA-256 matches that digest.
- KR cache evidence IDs accept six-digit KRX tickers with `.KS` or `.KQ` only,
  and cache matches are resolved again before use to reject external symlinks
  or candidates outside the evidence-cache root.
- Recoverable API failures expose stable generic error descriptions, not exception text or
  local filesystem details. Logs also omit raw exception messages that could
  contain credentials, paths, or provider payloads.

## Frontend information architecture

- The core loop is search -> Historical valuation -> Forecast scenario -> Fact
  Audit.
- Snapshot is a dense FnGuide-style company overview. The Ask/underwriting hub
  is a separate task surface, not a replacement for Snapshot.
- Historical-only metric, period, and chart settings do not persist across
  unrelated workspaces.
- Navigation must keep the active item stable. Lower-frequency research tools
  use an explicit overflow model instead of moving the active tab between rows.
- DOM order and visual order must match for major regions. CSS `order` is not a
  substitute for correct semantic structure.
- The canonical viewports are 1440x900, 1024x768, and 390x844. Page-level
  horizontal overflow is a release blocker.

## Operations and deployment

- Local ingestion runs outside web request paths through the operator CLI.
- Public CI and manual operator workflows contain no secret values. They use
  commit-SHA-pinned actions, read-only repository permission, disabled checkout
  credential persistence, and environment-mediated workflow inputs.
- Manual ingestion workflows reference GitHub secret names only. Dispatch
  remains limited to repository writers, while local operator CLI commands stay
  the default beta path.
- API credentials are injected through environment variables or a secret
  manager. Values are never committed, logged, echoed, or embedded in docs.
- Deployment readiness, provider authorization, live ingestion, and an actual
  end-to-end source-backed response are separate gates.
- Docker Compose model validation is not proof of an image build; an image build
  is not proof of a live provider-backed research flow.

## Current priority

1. Complete the source-backed KR priority-universe ingestion and audit loop.
2. Run the Claude Design handoff and close every P0/P1 item in the companion QA
   checklist.
3. Verify local Docker images and a source-backed KR E2E with operator-provided
   credentials.
4. Add optional protected cloud deployment only after the local beta gates are
   green.
# Public dashboard — 2026-09-24

- Add a public `/stocks` product and an independent `services.market_dashboard` API/worker. Preserve the existing private terminal and its storage.
- Use the user-approved design contract in `docs/PUBLIC_DASHBOARD_DESIGN.md`; scoped tokens override legacy colors only within this product.
- Public-source snapshots are never silently replaced by fixtures. Missing, stale and unsupported facts remain explicit.
- Local SQLite is the zero-setup development backend; the same SQLAlchemy tables support PostgreSQL in the isolated deployment profile. Redis is optional locally and configured in Compose.

## Public dashboard revision — 2026-09-24

- User selected a Naver Finance-style, ranking- and company-first information hierarchy. Remove the provisional Hannun brand and teal palette; use a neutral functional title until a brand is chosen.
- Compact index sparklines expand on demand. Company pages put prices, charts and metrics before the expandable business description. Add source-provided company logos, themes, return periods, financial charts and DART statements.
- Reuse the existing OpenDART credential on the existing VPS. Public brokerage aggregates are connected; authenticated KIS is separately optional and remains unconfigured.
- Local accounts and explicit cross-device list merging use server-side Auth.js plus a private signed API. Recovery keys are displayed once. No trading authorization is attached to these accounts.
- Deploy as a separate Compose project, PostgreSQL database, Redis cache and Nginx host. Original private-terminal routes are not published by the new host. Runtime secrets and user accounts are not part of the source artifact.
- Keep last-good snapshots available during background refresh. Preserve source traces and exact timestamps for internal audit, while keeping source names, collection/update timestamps, and provider limits off normal user screens. Show a generic unavailable state when no usable value exists.
- Keep KIND-listed KONEX securities in the searchable universe and give them a durable rotating detail/quote collection cursor. Use the same security-scoped public quote endpoint that serves the other Korean listings; preserve absent trading value as null rather than estimating it from close multiplied by volume.
- DART company search is `/dsab001/main.do`, verified with a stock-code query; the older `/dsab002/main.do` redirects to an error page.

## Public market access revision — 2026-09-25

- Remove public-market sign-in, account creation, password recovery, OAuth, server account sync, and their browser/API routes. Every visitor can search and view without an account.
- Keep favorites, groups, saved views, theme, and display settings in browser-local storage. Cross-device synchronization is out of scope for the public surface.
- Do not expose or log legacy account rows while retiring these routes; preserve existing database contents. The private `/terminal` authentication remains separate and unchanged.
- Add an optional, read-only Kiwoom execution WebSocket for recently opened securities. Keep provider credentials in the worker container only, retain public polling as fallback, and never call order endpoints.
- Keep the account-key allowlist as an external activation gate. The current workstation token request was explicitly rejected because its IP is not registered; the production server handshake is still unverified. Do not mark the public production feed live before a successful handshake.

## 2026-09-26: independent history queue and bounded snapshot cache
- Price history now has its own durable worker queue, sharing the existing public-provider request limiter. Recently viewed symbols precede a fair cursor through all listed equities; failures back off without overwriting a good chart. Disk headroom defers background backfill before capacity exhaustion.
- Decoded snapshot cache is LRU bounded to 128 entries and 32 MiB of serialized payload by default. Actual Python heap size is larger; this is a cache budget, not a process RSS claim. Redis and persisted snapshots remain the source of current revisions.
- Comparison uses calendar periods and only exact common trading dates. Returns exclude dividends and retain original source traces and deterministic formulas internally.
- CSV export preserves the entire filtered universe regardless of UI pagination. Guest preferences can be copied between devices by a validated, additive backup import.
# 2026-09-26: Mobile-first product priority

The active public-dashboard design is `docs/market-product-plan-v3.md`. The primary flow is search, price/chart, investor flow, favorite, and return on a phone. Existing comparison and financial depth remain available, but expanding first-screen controls is not a completion criterion. Short-selling trades, reported net short holdings, securities lending, and margin balances are distinct data products; no one substitutes for another. Provider publication dates remain part of each figure's meaning, while operational provenance stays out of the default user interface. Broker tick ingestion, source coverage, deployment, automated checks, and actual-user task completion are separate release evidence.
## 2026-09-26 mobile activity and short-selling implementation

Mobile detail uses four primary destinations with secondary price/company tabs. Investor summaries and touch date controls share the same observations as the expandable table. Missing investor observations prevent a false complete-period total. Brokerage rankings keep absent buy/sell quantities distinct from zero, and the whole membership table remains available below the first ten entries.

Short-selling collection runs independently from DART and history collection. KRX security identity is resolved from the exact short code; daily trades and reported net-short balances use separate snapshots and dates. KIS daily trades can back up the trade section but never manufacture a balance. Authentication is shared with the existing KIS brokerage collector. Provider circuit breakers, persistent collection progress, disk-space guards, bounded freshness, and last-good retention limit failed requests and storage growth. Public responses omit internal traces while keeping them in storage. Actual source receipt and deployment remain unverified under the current network restriction; see `docs/mobile-mts-implementation.md`.

The shared market read model retains only universe/enriched/quotes inputs and reuses unchanged revisions. Startup warms the initial model. SQLite revision reads explicitly use a covering key/updated_at index because its planner otherwise prefers the unique primary key and touches large JSON overflow pages. Keys stay parameterized; PostgreSQL retains its existing SQLAlchemy path. Local response timings do not establish production load capacity.

## 2026-09-26 financing and stream recovery

Lending, credit financing and stock loans use distinct source fields and remain within the existing mobile activity section. Settlement and trade dates stay separate; unverified amount units are not exposed. Date pagination is bounded and returns partial evidence on mid-page failure. Missing credentials suppress background requests and remain unavailable, never zero. Worker-only configuration loading uses an explicit allowlist and preserves process-environment precedence. Small revision-aware security identity caches are shared by background queues. Kiwoom readiness requires subscription acknowledgement; duplicate and old ticks do not cause redundant snapshot writes, and closing an idle socket releases subscriptions. See `docs/financing-implementation.md` for verified tests and external blockers.

## 2026-09-26 application-only release runner

Deployment uses immutable source releases, hash verification, a verified PostgreSQL archive and preserved previous image tags. Only this project's three application services are replaced. Database, Redis, volumes and unrelated services remain outside mutation commands. Failed build leaves running apps intact; failed activation/readiness restores previous app images. Transport uncertainty, local server checks, public browser checks and source-data readiness are distinct states. User authorization is present, but the current network permission tool returned no network grant; no alternate transport bypass is attempted. See `docs/release-runner.md`.

## 2026-09-26 detail response reuse

Public stock detail responses are cached by all backing snapshot revisions, bounded to 128 entries and 16 MiB of compressed bodies. The database revision path bypasses short-lived Redis values to avoid pinning stale data under a new revision. Compression is outside the shared market model lock; concurrent requests coalesce through bounded striped locks. The mounted detail page uses a weak ETag to skip unchanged JSON transfer and React state replacement; quote polling remains independent. No market data is persisted in browser storage by this optimization. Paired warm HTTP measurements showed 3.12–4.16x full-response and 4.29–8.08x unchanged-refresh improvement, not the requested 10x whole-product performance. See `docs/detail-performance.md` for evidence and exclusions.

## 2026-09-27 progressive detail and visible subscriptions

The large stock heading remains in normal document flow at every breakpoint. Per-stock chart preferences are local, URL selections win, and favorite undo preserves group membership. More/settings use one mounted dialog so switching views does not pop browser history and close the destination.

The legacy full-detail contract remains available. Summary and deeper sections are separate responses; static-section validators exclude quote revisions and return only security identity, while summary retains current security data. Loaded price history survives summary refresh. Server-side quote rows are shared across socket subscribers with per-security sequences and an epoch. Worker snapshot revisions are polled at 250 ms; this does not establish provider streaming availability. Clients pause hidden subscriptions, reconnect with bounded delay, resync through REST, and prevent an older REST response from replacing a later stream event.

Read-only KIS book/execution collection is independent, demand-driven, bounded and unavailable without configured credentials. Source fields are documented in docs/completion-20260927.md. Existing collection workers must restart to activate changes; code and live data remain separate gates. Detailed DART backfill now plans five completed years plus the current year; one-year filings use persisted pagination. TTM and full actual-period coverage remain open.

## 2026-09-27 휴대폰 조작과 기간 기반 분석

보통·크게·더 크게 설정은 기존 large 값을 읽는 방식으로 확장했습니다. 종목 행의 비교 선택은 명시적으로 켜며 검색창을 항상 노출합니다. 팝업 교체 시 이전 history.back 완료 이후 새 기록을 추가해 글자 크기 창이 닫히는 경쟁 상태를 방지합니다.

분석 API는 연간 EPS/BPS와 결산월 말 가격을 비교합니다. 당시 알려진 정보만 쓰는 백테스트라고 표현하지 않습니다. 사용자 가정 계산은 실제 예상실적과 분리합니다. TTM 입력은 검증된 기간·기준·통화·출처를 요구하며 잔액·비율을 합산하지 않습니다. OpenDART 기간 수집은 worker의 별도 배치이며 사용자 조회 경로에서 외부 API를 호출하지 않습니다. 원본과 정정 이력은 보존합니다.

키움·한투는 새 키 발급 이후 연결합니다. 공식 MCP는 개발·조회 도구로 구분하며 대시보드의 시세 요청마다 MCP를 실행하지 않습니다. 설치 파일, 인증, 실제 장중 수신, 공개 배포는 별도 상태입니다. 상세 기록: docs/implementation-20260927-phone-analysis.md.

## 2026-09-27 조회 전용 증권사 연결

API 신청·설정은 사용자에게 위임받았습니다. 본인 로그인은 증권사의 실제 인증 화면에서 진행합니다. 한투 서버 어댑터는 6개 기존 시장 조회 경로와 거래 ID의 명시적 조합만 허용하고, 주문·계좌·미등록 경로는 인증 요청 전에 차단합니다. 새 데이터 기능은 원천 명세 검증 후 이 목록에 추가합니다. 신청 UI 오류, 보안프로그램 요구, 도구 설치 제한과 인증 성공은 별도로 기록합니다.

## 2026-09-28 Coin Desk 구조의 국내 주식 적용

참조 작업 `01a0b9fd-9c01-7b62-9477-4f09c0f6d97c`의 0.17.1 코드에서 기간 수익률·낙폭·월별 변화, 설명에서 실제 차트로 이동, 동일 데이터 쓰기 생략, 전 테이블 백업 복원 검사를 적용했습니다. 원본 작업은 읽기만 했으며 국내 주식의 휴장·시장·조정 기준을 별도로 처리합니다. `docs/coin-desk-adaptation-20260928.md`가 구현과 검증 기록입니다.

새 가격 분석은 저장된 실제 종가를 사용하는 별도 접힘 패널입니다. 선택 구간 시작부터의 관측 최고 종가로 낙폭을 계산하며 전체 상장 기간 고점으로 표현하지 않습니다. 직전 월이 없는 월별 수익률은 null이며, 배당 포함 가격은 가격 수익률 입력으로 받지 않습니다. 수집·조정·거래일 검증을 계산 가능 상태와 혼동하지 않습니다. 코인 파생 지표·투자 점수는 가져오지 않습니다.

스냅샷의 `updated_at`은 내용 revision입니다. 동일한 canonical JSON은 revision·캐시·알림을 변경하지 않으며, 실제 수집 시도·성공은 기존 작업 상태와 원천 기록에 남깁니다. SQLite와 PostgreSQL은 조건부 원자적 upsert를 사용합니다. SQLite 실제 동시 저장은 검증했으나 PostgreSQL 실행 검증은 별도입니다.

백업은 읽기 트랜잭션의 일관된 시점을 사용하고 모든 사용자 테이블과 스키마를 발견합니다. 작업 디렉터리의 정렬된 고정 크기 행 해시를 비교하므로 대용량 payload를 메모리나 제한된 OS 임시 파일에 정렬하지 않습니다. 운영 DB를 덮어쓰지 않고 별도 복원한 데이터로 검사합니다.

로컬 v22에서 173개 회귀 검사, 2,872개 저장 가격 분석, 약 2.50GB SQLite 복원 검사를 통과했습니다. Chrome 너비·큰 글씨·라이트/다크 검증과 실제 Android/아버님 사용 평가는 구분합니다. 웹 경유 검색 p95 420.56ms는 300ms 목표 미달이며, 새로운 실제 증권사 연결·TTM 자료·공개 배포 완료를 뜻하지 않습니다.

## 2026-09-28 검색 처리와 연결 재개

검색만 Node API Route로 처리하고 Next 화면·다른 조회·WebSocket 경로는 유지합니다. 웹의 검색 스냅샷은 원천 조회 시작부터 최대 250ms, 최대 256항목·4MiB이며 적중으로 만료가 늘지 않습니다. 오류·지연 응답은 보관하지 않고 gzip 본문은 한 번 만들어 공유합니다. 백엔드는 시간 캐시를 추가하지 않고 동시 조회만 공유하여 다음 요청에서 DB revision을 다시 확인합니다. 로컬 v27의 100 동시 연결 검색 p95는 3회 184.40/148.10/105.65ms였으며 공개 성능·전체 10배 개선으로 확장하지 않습니다.

지정 종목 DART 재수집은 전체 시장 진행과 분리된 안정적인 체크포인트를 사용합니다. 회사 식별 요청의 실패도 단계와 재시도 시각을 보존합니다. 연간 사실을 네 분기로 복제하거나 금액 계정 TTM을 주당 EPS 검증으로 승격하지 않습니다. 새 실제 수집은 환경 네트워크 차단으로 멈췄고 기존 정상 자료는 유지했습니다.

Windows 시작기의 CIM 조회 불가는 기존 PID 기록 보존에만 한정해 처리합니다. 이 경로는 기존 프로세스를 종료하거나 제어하지 않습니다. 종료 스크립트의 소유권 검사는 완화하지 않았습니다. 최신 배포 패키지는 로컬 점검 페이지·키·DB·빌드 디렉터리를 제외합니다. SSH 연결 실패와 로컬 빌드 성공을 배포 성공으로 합치지 않습니다. 상세 결과: docs/search-live-ttm-release-20260928.md.


## 2026-10-03 Toss PC 공개판 v10

최신 사용자 요청에 따라 Toss를 유일한 증권사 연결로, DART와 검증된 공개 기업 자료를 보완 원천으로 유지한다. 과거 모바일 우선·키움 필수 문구는 현재 출시 기준이 아니다. 기업 분석 6개 목차를 사용하며 검색 색인은 회사 식별 정보만 포함하고 현재가를 보관하지 않는다. 공유 링크의 분석·가치평가 선택값은 목록 URL 재작성에서도 유지한다.

company/fundamentals 응답에서 원본 XBRL·전체 보고서를 분리하지만 기존 전체 상세 계약과 저장 자료는 유지한다. TTM 요약은 같은 최신 종료일의 회사 전체 금액만 표시하며 사업부문·과거 현금흐름을 합치지 않는다. 주당지표·분할·공개 시점 근거가 없으면 가격 비교선을 생성하지 않는다.

Toss 보완 수집은 단일 worker의 인증과 호출 예산을 공유하고 종목·자료별 커서를 저장한다. 가격 10년과 거래 자료 우선 1년은 실제 공급 범위까지 이어 수집한다. 최신 페이지는 과거 행과 이전 페이지 커서를 지우지 않으며 행별 출처를 유지한다. 날짜 없는 가격·빈 응답·오류를 정상 최신 0으로 대체하지 않는다. DART 기간 수집을 느린 기업 상세 처리와 분리하고 사실 저장은 100개씩 충돌 무시 삽입해 정정·중복 불변성을 유지한다.

배포 v10, Python 94개와 웹 빌드 통과, 1440px Chrome 검수는 확인했다. 전 종목 장기 수집·장중 새 이벤트·주당지표 검증·WAN 성능·실사용 완료와 구분한다. 측정 및 남은 항목: docs/remaining-development-20261003.md.

### v11 기업 자료 읽기 모델

원본 official 스냅샷과 company/fundamentals 전용 읽기 모델을 같은 트랜잭션에 저장한다. 별도 source_revision이 일치할 때만 읽기 모델을 사용한다. 기존 자료는 원본을 보존하며 첫 조회 때 변환한다. 큰 XBRL을 소개 화면마다 역직렬화하지 않고, 원본 회계 사실 조회·계산 경로는 유지한다. Python 97개 통과, 공개 v11 반영.

### v12 스트림 복구 상태

이전 연결의 구독 승인 목록을 새 연결의 증거로 재사용하지 않는다. 연결·REST 복구·구독 요청·실제 승인을 각각 기록하고 한 종목 스냅샷 실패는 해당 종목에 격리한다. 검증된 스냅샷 없는 종목은 재시도하며 다른 정상 종목은 구독한다. 장중 새 체결 검증은 별도 게이트로 유지한다.

### 2026-10-03 v18 차트·과거 자료·저장 개선

하루/장기 가격은 동일한 Lightweight Charts 기반의 선·영역·캔들·OHLC로 제공한다. 주·월봉은 전체 관측 구간 집계 후 달력 기간으로 자른다. 5분봉은 실제 분봉만 집계하고 하루/1주 선택으로 관측 범위를 바꾼다. 현재 시각으로 오래된 거래일을 대체하거나 결측 OHLC를 생성하지 않는다.

실적은 막대만 보여주지 않고 원값·전년 동기 증감률·이익률 표를 함께 제공한다. DART 전체 보고서 수집은 공시 TTL에서 분리하고 선택한 기간을 우선 처리한다. 2015년 이후 보고서 선택 기능과 전체 자료 확보 완료는 별개다. 투자자·프로그램 자료의 페이지 커서를 끝까지 이어가되 시장과 단위를 혼합하지 않는다.

전체 재무 사실을 여러 JSON 읽기 모델에 중복 보관하지 않는다. 원본/정정 사실의 존재를 검증한 뒤 계산용 읽기 모델만 축소한다. 10개 회사 원본 사실 337,159건은 보존됐고 읽기 모델 합계는 97.55% 감소했다. 이 수치를 서비스 전체 속도 향상으로 바꾸어 표현하지 않는다.

DB 메모리 부족으로 백업이 중단되는 문제를 수정했고 별도 DB 복원·PC 암호화 사본의 해시 일치까지 확인했다. 컨테이너 메모리 합계 4GiB와 다른 서비스 격리를 유지한다. 상세 검증과 미완료 범위는 `docs/remaining-development-v18-20261003.md`를 따른다.


### 2026-10-03 v24 기간 조회·조회 옵션·재무 검증

첫 상세 화면에서 수십 년 가격 원본을 자동 내려받지 않는다. `/price-window`는 정규화 저장소에서 기간별 자료와 계산 여유분만 읽고, 기간 수익률은 5개 기준일을 조회한다. 기존 `/prices` 전체 계약은 보존한다. 주·월봉은 첫 표시 기간을 온전히 집계하고 장기 거래정지가 있어도 20개 관측 기간으로 이동평균을 계산한다.

토스 환율 시점·방향, 장 일정 날짜, 상장 상태·주식 종류는 요청과 캐시 키를 함께 분리한다. 시장 수급의 기관 세부는 매수/매도에서 순매수를 계산하며 결측을 0으로 만들지 않는다. 기존 종목 목록과 별도의 공급자 목록을 대조하고 공급자 목록만으로 종목을 삭제하지 않는다.

TTM은 최신 보고기간의 준비 상태로 판단한다. 공개일·회사·회계 차원이 충돌하면 입력을 거부한다. 주식분할 기준 검증이 없으면 비교선을 생성하지 않는다. 배당 예정일과 지급 확인을 분리하고 실제 지급 증거 없는 재투자를 막는다. 파서 변경은 검증된 원본 파일을 재사용하며 자료 전체 확보를 의미하지 않는다. 상세 기준: docs/releases/20261003-v24/ACCEPTANCE.md.

### 2026-10-03 v25 다중 지표와 실제 자료 검증

EPS·희석 EPS·OCF·FCF·EBIT·EBITDA·매출/주를 같은 지표 정의와 실제 XBRL 입력으로 계산한다. EBIT는 세전이익과 이자비용을 우선 사용하며 영업이익으로 대체하지 않는다. DART의 ReportedAmountMember는 전체 보고 금액 축으로만 허용하고 사업부문 사실을 합산하지 않는다. 비교 배수는 EPS 15배, BPS 1배 외에는 검증된 과거 표본 또는 사용자 입력이 필요하다. FCF·FCFE·AFFO를 서로 대체하지 않는다.

삼성전자 IR의 8년 자본 변동 표와 2018년 분할, 2025·2026년 자기주식 소각을 원문 해시와 함께 보존한다. 검토된 2019-01-01~2026-06-30 구간에 한해 원주가와 동일 주식 단위의 재무를 연결한다. 이후 날짜나 실제 분할을 가로지르는 구간에는 자동 확장하지 않는다. 주가선은 계속 제공하고 비교 불가 숫자를 브라우저에서 재생성하지 않는다.

차트 인스턴스는 지표·배수 변경 때 재사용하며 데이터 갱신으로 발생한 가상 crosshair 이벤트는 선택 날짜를 변경하지 않는다. 차트·읽기 영역·CSV는 같은 검증된 결과를 사용한다. 최초 표시 실적 5개와 전체 저장 자료를 구분한다.

Cloudflare는 별도 workers.dev 시험 주소에서 기존 원점을 읽는다. 고정 원점만 허용하며 오류·호가·체결·WebSocket은 공유 응답 캐시에서 제외한다. 현재가가 포함된 검색과 요약은 원점 검증 시각을 포함한 최대 1초로 제한한다. 이 배포는 기존 sslip.io 주소가 Cloudflare로 이전된 것을 의미하지 않는다.


## 2026-10-03 — 남은 자료 연결 v26
- 최신 수집 시각과 과거 페이지 수집 시각을 분리한다. 오래된 커서는 날짜가 바뀌어도 5회 중 4회 전진하고, 1회는 최신 페이지를 갱신한다.
- 404는 최신 토스 종목 목록과 대조해 재시도 가능한 진단으로 저장한다. 목록에서 종목을 삭제하거나 영구 미제공으로 처리하지 않는다.
- 실시간 구독 응답과 실제 체결·호가 메시지 증거를 분리한다. 수신 원문·지연·연결 세대를 일별로 보관하며, 화면 반영과 누락 없는 복구를 증명했다고 표현하지 않는다.
- SK하이닉스·현대차의 사업보고서에서 확인한 무분할 기간만 주당 기준에 적용한다. 검토 종료일 이후로 자동 연장하지 않는다.
- 삼성전자 2024/2025 연결 FCFE는 2025 사업보고서의 현금흐름과 리스 주석을 검토한 경우만 계산한다. 지급이자는 OCF에, 리스 원금은 차입 상환에 이미 포함되어 중복 차감하지 않는다. 다른 회사·분기·공시에는 이 분류를 전용하지 않는다.
- 배당 지급 예정일은 실제 지급일로 승격하지 않는다. 정확한 지급일·배당락일·기업행사 구간 검증이 없는 배당 재투자는 계속 차단한다.


## 2026-10-04 — 과거 공시 대기열과 분할 단위
- 공시 목록과 XBRL 대기열을 연결하고 전체 종목 목록 수집에 독립 커서를 사용한다. 비12월 결산 분기코드를 월로 추측하지 않는다.
- 분할 변환에는 공시별 원 주식 단위 확인이 필요하다. 이미 소급 수정된 수치를 중복 조정하지 않는다. 원본은 보존하고 계산 식별자·오차 한계도 함께 변환한다.
- 구형 EPS의 shares 단위 오류는 원문 확인 없이 수정하거나 주식 수로 취급하지 않는다.

## 2026-10-04 — 배당의 필드별 증거 연결
- 배당 보완 관측은 종목·주식 종류·기준일·통화·주당금액이 모두 일치할 때만 원공시에 연결한다. 확인된 공개 시점과 원문 해시를 요구한다.
- 예정일과 실제 지급일을 따로 보존하고, 실제 지급일보다 앞서 공개된 문서는 지급 완료 근거로 사용하지 않는다. 날짜가 충돌하면 계산하지 않는다.
- 출처가 달라도 같은 배당 권리는 두 번 합산하지 않는다. 정기·특별 배당의 같은 기준일 구성은 근거를 갖춰 통합한 뒤 계산한다.
- 배당 근거 추가 시 공시 수집 완료 캐시를 무효화해 새 근거를 반영한다. 개별 배당 확인은 전체 기업행사 구간 확인을 대체하지 않는다.

## 2026-10-04 — 거래 이력의 증분 저장과 범위 조회
- 토스 거래 자료는 새 페이지의 관측만 시계열에 upsert한다. 기존 JSON과 날짜별 출처 맵은 보존하며 새 관측은 행별 출처를 저장한다.
- 프로그램 매매 요약은 최신 한 행만 읽는다. 수급·신용·대차·공매도 기존 화면용 투영은 유지한다. 이 투영의 전체 재생성 비용은 별도 남은 최적화 대상이다.
- 투자자·프로그램 기간 조회는 DB 날짜 범위를 사용하되 전체 확보 시작일·종료일은 시계열 메타데이터에서 제공한다. 화면 기간과 저장 범위를 혼동하지 않는다.

## 2026-10-04 — 토스 미일치 종목의 보완 일봉
- 네이버 공개 일봉의 요청 2,600행 제한을 풀고 20,000행을 요청한다. 실제 제공 응답은 조사 표본에서 최대 3,000행이었으므로 짧은 응답만으로 전체 과거 확보라고 판정하지 않는다.
- 같은 원천의 기존 더 오래된 일봉은 보존하고 정규화 시계열에 증분 반영한다. 빈 응답·다른 원천의 응답으로 기존 자료를 덮어쓰지 않는다.
- 404 종목의 네이버 일봉 확보와 토스 수급·호가 등의 지원 상태는 별개다. 보완 일봉에 토스 출처나 검증된 분할 기준을 붙이지 않는다.

## 2026-10-04: first-screen projection and measured limits (v32/v33)
Keep detail originals intact; atomically project company/core actual financials to detail-summary. Backfill with source-row locks and retain legacy fallback. Summary no longer reads full flow histories. Public 100-concurrency measurements still miss targets; do not equate smaller stored projections with faster public service. Profile isolated full-universe snapshot read overhead; remove redundant JSON size serialization and Redis writes when use_redis=False. Evidence: docs/releases/20261004-v32/ACCEPTANCE.md.


## 2026-10-04: incremental enrichment projection (v34)
Preserve the original enriched snapshot for compatibility. Publish the changed security projection and source revision atomically while holding the source row lock. Read only changed member payloads; fall back to the original if revisions differ. Backfilled 2876 securities and verified equality during a real worker update. The measured internal read reduction did not meet public WAN performance targets; see docs/releases/20261004-v34/ACCEPTANCE.md.


## 2026-10-04: application timing and memory pressure (v35)
Expose app response-header duration independently of WAN latency on public search/summary; benchmark records both, without subtracting unrelated percentiles. Observed API swap ~202MiB and cgroup max events; raise API to 1280MiB on existing VPS. App-only limits total 3.25GiB; including existing stock DB/Redis the total is 4.375GiB, above the initial 4GiB by 384MiB. No other project limits changed. Burst verification passed without swap/OOM but long-duration validation remains. Confirmed dividend evidence remains absent; special-only payment evidence cannot verify regular+special aggregate. See docs/releases/20261004-v35/ACCEPTANCE.md.



## 2026-10-04 v36 과거 일봉 조회 단위

전체 순환마다 한 페이지만 진행하는 대신 기존 단일 토스 작업자에서 최대 8페이지를 이어 조회한다. API 호출 제한은 그대로 유지한다. 최신 페이지 확인은 한 페이지로 제한해 깊은 과거 커서를 보존한다. 실패 시 검증된 묶음과 재개 커서를 저장한다. 실제 NAVER·현대차 자료 증가를 확인했으나 전체 역사·성능·장중 검증 완료로 취급하지 않는다. 상세 근거는 docs/releases/20261004-v36/ACCEPTANCE.md.


## 2026-10-04 v37 거래 자료 과거 조회

5종 거래 자료를 기존 토스 작업자에서 최대 8페이지씩 받는다. 정상 페이지별 커서를 저장하고 화면 표는 묶음당 한 번 생성한다. 최신 조회는 한 페이지로 깊은 커서를 보존한다. 공유 호출 제한과 전 종목 순환은 유지한다. 관련 61개 테스트 통과 및 공개 배포. 실제 자료 증가와 잔여 완료 기준은 docs/releases/20261004-v37/ACCEPTANCE.md 참조.


## 2026-10-04 v38 공식 IR 지급일 보완

현대차 공식 IR의 검토한 보통주 배당 표를 수집하고 DART의 동일 종목·기준일·금액 이벤트에 지급일 근거를 연결했다. 실제 지급 확인 0→16건, 배당락일 미확인으로 검증 완료는 0건을 유지한다. 우선주와 다른 기업을 임의로 매핑하지 않는다. 원문과 관찰 시각을 보존한다. docs/releases/20261004-v38/ACCEPTANCE.md 참조.


## 2026-10-04 v39 우선주 배당

현대차 IR의 주식 종류 탭과 등록 목록의 정확한 이름을 대조한 명시적 4종목 매핑으로 자기 배당 열을 저장한다. DART가 없는 우선주에도 원문 배당 이력을 제공하며 권리 중복을 막는다. 공식 원문의 1년 이상 지연 지급 행은 근거 보완 전 자동 확정에서 제외하고 기존 리뷰 이력을 보존한다. 최종 공개 버전 v39a, 테스트 20개. docs/releases/20261004-v39/ACCEPTANCE.md 참조.


## 2026-10-04: Reviewed preferred-share company documents

Company-wide DART disclosures and profile can be reused through an explicit reviewed issuer registry (initially the four Hyundai IR-listed securities), with exact listing-name checks and native DART mapping precedence. Prices, dividends, statements and per-share facts are not aliased. Keep original document provenance and record issuer_link separately. Share fresh company disclosures rather than spending the API budget once per share class. Preserve last good documents on refresh failure. See docs/releases/20261004-v41/ACCEPTANCE.md.


## 2026-10-04: Reviewed splits in corporate-action ledger

Reuse security-specific reviewed share-unit split histories in the event/performance ledger, with current DART company identity and source-hash checks. Preserve input evidence and existing events; conflicts block coverage and are retained for review. A verified split never promotes all corporate-action or dividend coverage. Effective date and changed-share listing date remain distinct. See docs/releases/20261004-v42/ACCEPTANCE.md.


## 2026-10-04: Trading page availability and history completion

A source snapshot can be ready for display while retaining a provider history cursor. Queue readiness requires backfill_complete and no next_until; otherwise record collecting with separate source_status. Empty final old pages complete the history; empty current head responses remain stale without dropping saved values. Repair old queue interpretations in bounded atomic batches, archive old state, and preserve source rows/cursors. See docs/releases/20261004-v43/ACCEPTANCE.md.


## 2026-10-04: Security-first provider history scheduling

Process seven history kinds for one security before moving to the next. Preserve the legacy next cursor/phase pair and all dataset cursors, retries and current-head clocks. Retain one-in-five active-view priority, bounded bursts and shared provider limits. Schedule version 2 describes order, not source completeness or total throughput. See docs/releases/20261004-v44/ACCEPTANCE.md.


## 2026-10-04: Empty intraday chart recovery

An empty terminal response is not an indefinite loading state. Offer explicit daily-history navigation on intraday failure/absence without relabelling public OHLC as Toss integrated intraday data. Keep provider-specific 404 jobs incomplete; stored public prices only prove that dataset. See docs/releases/20261004-v45/ACCEPTANCE.md.


## 2026-10-04: Trading-day-aware turnover facts

An old enrichment must not overwrite same-day listing turnover. Select dated volume/value observations against the quote's Korean trading day, keep genuine zero, and return null for unknown/different periods. Preserve original facts and source market scope; do not use ingestion clocks or daily bars as realtime cumulative turnover. See docs/releases/20261004-v46/ACCEPTANCE.md.

## 2026-10-04: listing-aware backfill registration

Register missing security/data-kind jobs as queued on startup and listing changes. A queued job is not source availability or completion. Reserve otherwise-unused one-in-five priority turns for queued items after the first full cycle; active user requests retain precedence. Persist the next security identity alongside the legacy numeric cursor so a newly inserted listing cannot shift the next scheduled security. Reconstruct readiness from already fresh, provider-complete stored metadata without repeating the supplier query. Preserve data cursors, raw archives and per-item/global retry budgets.

## 2026-10-04: common analysis selection gates

Use the same structural period/security/currency/share-unit/venue eligibility function for valuation and metric selection. Report observed periods separately from comparable annual/TTM periods; do not advertise a quarterly-only or unverified filing as eligible for annual multiples. The metric menu response depends on raw bars as well as adjusted prices and share-unit evidence. Price data withdrawal invalidates its comparison status without removing financial observations. Source calculation and historical-series facts remain unchanged.

## 2026-10-04: public API connection reuse and edge release alignment

Nginx reuses a service-scoped upstream pool for stock REST reads while WebSocket upgrades retain the existing separate route. The transformation checks the exact local API port and headers, retains a rollback copy, validates Nginx, and preserves other service configs and one-second response freshness. Public before/after measurements remain mixed and fail the latency goals; do not claim a measured service-wide speedup. Cloudflare stable-data HIT measurements are recorded separately from live search/summary MISS and origin processing. Align the edge cache release with the current backend to prevent old release cache keys from persisting across schema changes.

## 2026-10-04: bounded previous close and stream payload isolation

The first trade on a Korean trading date reads the latest normalized bar strictly before that date with reverse/limit=1, rather than hydrating lifetime candles. Cache the previous close against the actual price-series revision so a late history collection or correction is visible; normalize UTC event timestamps to the Korean date. Preserve source execution timestamps and raw payloads. Reject unsupported currency/invalid trade/book fields before replacing normalized last-good depth or quote data; null orderbook timestamps remain supported and do not become current market timestamps. Test official frame processing and API-side WebSocket/reconnect snapshots separately from actual market reception and rendered-browser verification.

### 2026-10-04 v59
전 종목 재무 큐에 배경 매 방문·관심 매 5방문 호출 배분과 종목코드 재개 지점을 적용했다. 운영 자체 DART 6000회 중단을 12000회 예산으로 조정하고 실제 공급자 020 JSON/XML은 계정 공유 중단 상태로 처리한다. 공급자 한도와 자체 예산을 구분하고 원본/기존 자료를 보존한다. 전체 재무/TTM 확보와 실시간 검증 완료를 주장하지 않는다.

### 2026-10-04 범위 변경 — 배당 재투자 비교 제외
사용자가 배당 재투자 비교는 필요 없다고 명시했다. 개발·출시 완료 조건에서 해당 비교를 제외하며, 기업행사·배당금·배당수익률·지급 이력과 주식분할 검증은 유지한다. 기업 분석의 배당 포함 수익률/재투자 패널 진입을 제거한다. 기존 계산 원본·기업행사 자료는 보존하며 핵심 시세/수급/재무/실적 차트의 완성에 집중한다.

### 2026-10-04 범위 변경 — 코넥스 제외
사용자가 코넥스 종목은 하지 말라고 명시했다. 코넥스는 검색·순위·상세·시장 선택과 모든 종목별 자동 수집/구독 대상에서 제외한다. 이전 가격·재무·원본·작업 이력은 삭제하지 않는다. 완료 범위는 코스피·코스닥의 기존 보통주·우선주로 변경하며 코넥스 수집·404는 남은 출시 작업으로 집계하지 않는다. 원천 종목 목록과 공개/운영 범위를 분리한다.

## 2026-10-05: 거래 자료 재처리 재사용
입력 시계열 버전과 성공 출력 버전이 같을 때만 전체 거래 이력 재처리를 생략한다. 정정·상태 변경·출력 변경·부분 실패는 재시도하며 원본 수집은 유지한다. 처리량 향상은 별도 실측한다. docs/releases/20261005-v66/ACCEPTANCE.md 참조.

## 2026-10-05: 공시일과 주식 단위 날짜 분리
공시·기간별 명시적 단위 검증이 있으면 그 단위 날짜를 사용하고 공시일은 과거 시점 가용성에만 사용한다. 포괄 무분할 구간을 이후 공시에 확장하지 않는다. read model v10. docs/releases/20261005-v67/ACCEPTANCE.md 참조.

## 2026-10-05: 원문 검토 EPS 표 보완
XBRL에서 연결되지 않는 보통주 귀속이익·주식 수는 검토한 공시 표의 해시·접수번호·행·보통주 열을 고정해 보완한다. 공시 EPS 대조 실패 시 등록하지 않는다. 원본과 FactStore를 보존하며 새 공시에는 매핑을 자동 확대하지 않는다. v68은 SK하이닉스·현대차 실제 입력 24건을 작업자에서 수집했다.

## 2026-10-05: 반기 공시의 명시적 주식 단위 검토
무분할 구간에도 공시·기간·회사·종목별 단위 근거를 허용하며 공개일을 단위 날짜로 사용하지 않는다. v69에서 SK하이닉스·현대차 최신 TTM EPS는 실제 입력으로 ready가 됐다. 가격 검토 구간 이후에는 최신 비교선을 생성하지 않는다.

## 2026-10-05: TTM 공개 응답의 근거 변경 감시
financials/per-share 캐시도 share-basis를 감시한다. 검증 근거 추가·철회 시 ETag와 응답을 즉시 변경한다. 삼성전자 반기 당기 두 기간만 명시적으로 검토해 최신 TTM을 연결했으며 원본 숫자와 가격 검토 종료일은 변경하지 않았다.

## 2026-10-05: 토스 수집량은 실제 HTTP 응답으로 측정
전 종목 수집의 `attempts`는 큐 방문 횟수이지 성공 요청이나 저장 데이터 증가량이 아니다. 토스 요청 레코드에 프로세스 세션별 HTTP 요청 수, 성공 응답 수·행 수·바이트, 상태 코드, 429·인증 재시도, 상류 응답시간, 공급자 대기와 원본 보관시간을 누적한다. 종목 파라미터와 인증값은 기록하지 않는다. 실제 저장 행 증가는 정규화 시계열의 DB 전후 집계로 따로 확인한다. 세션이 달라진 표본은 하나의 처리량 구간으로 합치지 않는다.

## 2026-10-09: 정정 분기와 계산 공개 시점, 배당 검증 상태

주당지표의 분기 환산에서는 최신 정정 누적자료가 오래된 단독 분기를 갱신할 수 있게 한다. 같은 공개 시점의 검증된 단독 분기는 우선하며, 앞선 누적기간만 뒤늦게 정정된 경우 이전 버전의 후속 누적액과 차감하지 않는다. EPS는 분자·분모·보고 EPS 입력이 모두 공개된 이후에만 사용하고, TTM 결과의 원천 추적에도 실제 계산 공개 시점과 전체 입력 ID를 기록한다. 분석 투영 버전 12에서 기존 원본을 재계산하며 원본·정정 자료는 보존한다.

연결/별도 문맥이 충돌한 보고 EPS는 제외한다. 같은 보고 EPS 금액이라도 주식분할 기준이 다르면 비교선으로 승격하지 않는다. 배당의 지급 확인 상태만 있고 실제 지급일이 없으면 검증 완료로 보지 않으며, 기존 날짜 충돌은 일부 자료만 재처리했다는 이유로 해소하지 않는다. 배당 재투자는 여전히 제품 범위에서 제외한다. 이 변경의 모의자료 회귀검사는 실제 전 종목 재무·배당 확보 또는 공개 배포 완료의 증거가 아니다.

## 2026-10-09: 날짜별 시장·뉴스·근거 관계
뉴스 제목 정정과 공급자의 종목 연결을 별도 테이블에 보존하고 날짜별 시장 요약은 거래일당 마지막 관측으로 제한한다. 과거 날짜 저장본이 없으면 현재 순위를 사용하지 않으며 거래시장과 자료 기준일이 다른 값은 합산하지 않는다. 같은 관련 기사 연결과 검토된 사업 관계를 구분하고, 후보는 사업 관계로 공개하지 않는다. 공개 조회는 DB 생성·쓰기·공급자 호출을 수행하지 않는다. 계약과 검증 범위는 docs/MARKET_CONTEXT_CONTRACT.md에 기록한다.

## 2026-10-09: 분석 불가 사유와 수집기 heartbeat
표시 구간에 양수·비교 가능한 실적이 있을 때만 배수 입력을 요구한다. 원문 미수집과 음수 실적을 배수 입력으로 해결 가능한 상태처럼 표시하지 않는다. 수집기 저장 상태가 ready/collecting이라도 heartbeat가 기본 15분 또는 전체 목록 갱신 간격의 3배를 넘으면 파생 운영 상태를 degraded로 반환한다. 원본 보고 상태·갱신 시각·정상 자료·확보율은 유지한다. 응답 생성 시각은 수집 진행의 증거로 사용하지 않는다.

## 2026-10-09: 우선주 배당과 독립된 날짜 근거
삼성전자우 배당은 검토된 회사 연결과 공시 부속표의 정확한 종목명·주식 종류·주당 금액을 모두 확인한 원문에서만 투영한다. 보통주 금액을 복사하지 않으며 기존 DART 아카이브를 재사용해 중복 수집하지 않는다. KIND 배당락 공시는 특정 접수번호의 배당 권리와 직접 연결할 수 있고, 실제 지급 확인을 선행 조건으로 요구하지 않는다. 예정일은 실제 지급일로 승격하지 않는다. 정확한 주권 종류를 먼저 확인해 현대차2우B 공시를 현대차 보통주에 적용하지 않는다. 또한 공식 조회용 투영 캐시의 디스크/읽기 전용 실패는 원본 계산 결과 반환을 막지 않으며, 운영 예외 클래스만 로그로 남긴다.

## 2026-10-09: 엣지 캐시의 신선도·복구 계약
Cloudflare 공개 조회 캐시에 private/Vary/쿠키 차단, 원점 Age·Date 반영, 캐시 장애의 원점 복구, 조건부 GET과 오류 no-store를 적용한다. 시세는 기존 최대 1초를 유지하고, 재무는 원점에서 confirmed로 명시한 경우만 공유 캐시한다. API HIT가 브라우저의 5분 수명을 새로 시작하지 않도록 재검증을 사용한다. 실제 배포·인증·성능 관측은 docs/releases/20261009-v89/edge-audit.md에 별도로 기록한다.

## 2026-10-09: 상장 기업 시장 공시 수집 범위
OpenDART 공시 목록의 corp_cls는 복수 값을 지원하지 않으므로 Y·K를 순차 조회해 접수번호로 중복을 제거하고 날짜·접수번호 내림차순으로 합친다. 기존 대상 종목 필터를 적용한 뒤 최신 100건을 공개한다. 각 응답과 병합 명세를 원본으로 보관하며 어느 한 조회의 HTTP·인증·형식 오류라도 기존 정상 공시 목록을 부분 응답으로 교체하지 않는다. 013은 해당 시장의 정상적인 자료 없음으로 구분한다. 원천 total은 두 시장의 제공 건수 합계이며 실제 표시 건수와 구분한다. 공식 계약: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001 . 모의자료 10개 회귀검사는 실제 공급자 재수집·공개 반영과 구분한다.

## 2026-10-09: 화면 요청과 예약 수집의 대기 분리
토스 시장 조회의 화면 요청은 긴 예약 작업 배치가 끝나길 기다리지 않고 2초 간격의 별도 루프에서 처리한다. 동일 클라이언트·인증·API 그룹 예산을 공유하고 작업 키별 잠금과 TTL 재검사로 중복 실행을 방지한다. 한 작업이 45초 안에 끝나지 않으면 오류로 기록하고 기존 정상 자료는 stale로 보존한다. 이는 요청 큐 대기를 줄이는 변경이며 공급자 호출이나 화면 갱신을 2초 안에 보장한다는 의미가 아니다.

## 2026-10-09: 유의사항은 실제 화면 요청으로 수집
요약 조회·미리 읽기로 활성화된 모든 종목에 유의사항 조회를 예약하지 않는다. StockWarnings의 실제 조회 요청을 기존 큐로 받아 60초 TTL과 오류 시 마지막 정상 자료 보존을 유지한다. 상하한가와 다른 API의 호출 한도·인증·동시성은 변경하지 않는다. 관측된 유의사항 116회·148.3초는 실제 화면 요청과 자동 예약을 함께 포함한 기존 세션 수치이며, 배포 후 절감량이나 전체 서비스 속도 개선으로 보고하지 않는다. 측정 한계는 docs/releases/20261009-v91/backfill-throughput-audit.md에 기록한다.

## 2026-10-09: 요청한 시장 수급 집계의 과거 페이지 재개
실제 화면에서 요청한 주·월·년 시장 수급만 기존 일별·지수 과거 수집과 순환하며 한 예약 순환당 이전 페이지 1개와 공유 호출 예산을 유지한다. 요청 기록은 화면 종료·재시작 뒤에도 과거 작업의 근거로 사용한다. 최신/과거 저장은 시리즈별 잠금으로 행 유실을 막는다. 명시적 nextUntil:null, 커서 생략, 최초 자료 없음, 기존 자료 뒤의 빈 최신 응답과 오류를 구분한다. 기존 자료 뒤의 빈 최신 응답은 과거 수집 완료를 뜻하지 않는다. 검증 범위는 docs/releases/20261009-v92/market-investor-history.md에 기록한다.

## 2026-10-09: 정적 화면과 API 배포 분리
화면은 Next.js 정적 export로 빌드하고 Cloudflare Static Assets와 VPS의 동일 산출물로 제공한다. 기존 standalone 빌드는 유지하며, export 작업은 격리된 임시 소스에서 API routes와 서버 리다이렉트를 제외한다. API·CSV POST·가정 계산·WebSocket은 고정 VPS 원점으로 연결하고 정적 자료에는 Worker를 실행하지 않는다. 이전 회사·terminal 주소는 공통 변환 함수로 종목·기간·필터를 보존한다. 오류·내부 경로는 HTML fallback으로 처리하지 않는다. 실제 배포 전 동일 버전·기존 주소·PWA·저장값·장중 스트림을 별도로 검증한다. 정적 빌드 성공은 공개 배포나 데이터 수신 완료를 뜻하지 않는다.

## 2026-10-09: 검토된 주식 수 변경 공시와 자동 연장 연결
주식 수 변경의 명시적 검토 기록은 회사·종목·접수번호·원문 해시별 불변 버전으로 저장한다. `record_review`는 기존 v83의 `effect/reason/source_trace` 객체를 그대로 받는 내부 검토 입력이며, 공시 제목으로 검토 결정을 생성하거나 `verified`를 추가하지 않는다. 읽을 때 공식 문서 URL의 접수번호, 원본 봉투의 URL·해시, 실제 보관 바이트를 다시 검사한 뒤에만 주식 단위 연장에 전달한다. 동일 접수번호의 근거 교체는 이전 검토 ID를 명시해야 하고, 새 접수번호의 정정 공시는 별도 검토가 필요하다. 원본·이전 검토·기존 주식 단위 근거는 보존한다.

`review_required`인 구간은 같은 한국 날짜와 검토 revision에서 공급자를 재호출하지 않는다. 명시적 검토 revision이 바뀌면 저장한 정확한 공시 구간을 재검증하고, 다음 날에는 같은 구간의 공시 목록을 다시 확인한다. 검토하지 않은 사건과 분할·병합 등의 단위 변경은 계속 차단한다. 이 연결은 신규 기업의 최초 주식 단위 검토, EPS 입력 확보, 최신 TTM 확인을 대신하지 않는다. 운영 DB에 새 검토를 등록하지 않은 상태와 실제 자료 확장을 구분한다.

## 2026-10-09: 시계열 출처의 공유 저장과 단계적 전환
기존 시계열은 읽기·재수집만으로 변경하지 않고 새로 추가되거나 값이 정정되는 행만 SHA-256 출처 참조로 저장할 수 있게 한다. 출처 JSON의 모든 필드·null·수집 시각·원문 해시를 보존하고, 같은 트랜잭션에서 참조와 행을 저장한다. 읽을 때 원래 dict를 복원하며 바이트·해시·참조가 불일치하면 오류로 차단한다. `MARKET_SHARED_SOURCE_TRACES=0`을 기본값으로 두고, 모든 API·수집기가 참조 읽기를 지원하는 버전을 먼저 배포한 뒤 별도 버전에서 `1`로 켠다. 활성화 후 롤백 하한은 읽기 호환 버전이며 이전 inline 전용 앱으로 되돌리지 않는다. 재무 사실 저장과 기존 행 대량 변환·삭제는 제외한다. 계약과 표본 한계는 `docs/SOURCE_TRACE_STORAGE.md`에 기록한다.


## 2026-10-09: 최초 기업의 EPS 입력과 주식 기준 검토 작업 연결
기존 검증 기업의 연장 작업과 별도로 국내 대상 종목을 제한된 순환 예산으로 조사한다. 실제 EPS 사실에서 기간·이익·가중평균 주식 수를 대조하고, 누락된 XBRL은 기존 요청 큐에 연결한다. 공급자 오류·미수집·회계표 조회 성공을 구분하며 성공 원본의 재해석은 회사·접수번호·해시·공개일 revision당 한 번만 수행한다. 공급자 재시도와 기존 단일 수집 예산을 지킨다.

기아 2025년 3분기·연간 연결 EPS 주석은 2026-10-09 직접 확인한 문서 해시, 보통주 표 머리말, 원 단위, 연결손익계산서의 실제 기간을 묶어 명시적으로 매핑한다. 원문·기간 문서·발행 회사·공개일이 맞을 때만 기존 reviewed fact 계약에 넣으며 정정 접수번호에 승인을 복사하지 않는다. 이 입력 연결은 주식분할 기준 승인을 뜻하지 않는다. 최초 주식 기준은 별도 검토 작업의 정확한 누락 근거로 유지하고, 근거가 없으면 TTM 비교값은 계속 null이다.

## 2026-10-10: PC 일일 전송과 실제 복원 검증 분리

기존 offsite_backup 진입점은 VPS의 완결 dump·manifest를 읽기 전용으로 전송하고 PC의 원본·독립 DPAPI 암호화본을 함께 보존한다. 원격·로컬 SHA와 전체 인증 복호화 통과 상태는 `transferred_encrypted_verified`이며 PostgreSQL 복원 성공과 구분한다. 기존 복구 영수증을 새 dump에 재사용하지 않는다. 일일 작업은 별도 DB를 생성하지 않고, 관리 용량 상한·최소 여유 공간·단일 실행 잠금·90분 전송 제한을 적용한다. 완료본과 실패한 partial을 자동 삭제하지 않으며 상한에 도달하면 `blocked_capacity`로 중단한다. Windows 작업은 현재 사용자·05:30 KST·네트워크 사용 가능·최대 2시간으로 명시적 등록하고 기존 LUXON 로컬 백업 작업은 변경하지 않는다. 절차와 운영 검증 경계는 `docs/PC_OFFSITE_BACKUP.md`에 기록한다.

## 2026-10-10: 거래 자료 투영의 필드별 재사용

공유 상세 JSON의 revision이 바뀌었다는 이유만으로 투자자·신용·대차 등 전체 이력을 다시 생성하지 않는다. 같은 원천 signature에서 출력 revision이 같으면 그대로 재사용하고, 다른 작업이 공유 JSON을 바꿨으면 해당 endpoint가 소유한 필드의 결정론적 SHA-256을 비교한다. 원천 revision·행 수·상태가 바뀌거나 소유 필드가 손상·누락되면 다시 생성한다. 실제 쓰기 전후 revision과 저장된 출력 해시도 대조해 중간 변경이 있으면 다음 처리에서 재검증한다. 원본과 정상 시계열·호환 JSON은 보존하며 이 변경을 물리적 디스크 절감량으로 해석하지 않는다. 상태 버전 2의 구현·격리 회귀·제한된 운영 관측은 `docs/releases/20261010-v103/ACCEPTANCE.md`에 구분한다.

## 2026-10-10: 거래 이력의 단일 저장과 검증된 화면 참조

기존 Toss 정규화 시계열을 복제하지 않고 화면 JSON의 장기 배열을 최신 요약·원천 참조로 줄이는 선택적 writer를 추가한다. 기본 flag는 끄고 legacy와 참조를 함께 읽는 버전을 먼저 배포한다. 원천과 화면의 완전 동등성 또는 이전 Toss projection의 소유 해시·모든 날짜·원문 hash 보존을 입증한 경우에만 변환한다. 미확인 값과 다른 원천의 행은 보존하며 양쪽 mirror 중 한쪽에만 남은 날짜도 공개 응답에서 유지한다. 같은 날짜 충돌에는 기존 normalized 우선순위를 유지한다. 짧은 트랜잭션에서 revision을 다시 대조하고 양쪽 출력·검증 marker를 원자적으로 저장한다. 원본·커서·원천 시계열·KRX 잔고는 변환 대상이 아니다. 쓰기 활성화 후 rollback 하한은 참조를 읽는 버전이며 flag 해제만으로 이전 전체 JSON이 복원되지는 않는다. 범위·검증·보류 조건은 `work/ttm-bootstrap/TRADING_HISTORY_COMPACTION.md`에 기록한다.

## 2026-10-10: 재무 사실의 수집 시각 보존과 공통 출처 저장

재무 출처의 `received_at`·`collected_at`·`ingested_at`은 각 사실에 원값 그대로 남기고 나머지 출처를 내용 해시로 공유한다. 공개 시점·정정·차원·단위는 공유 내용에 그대로 포함한다. 기존 시계열 형식과 구분한 `financial-source-trace-sha256-v1`을 사용하며, 읽을 때 원래 출처와 사실 ID를 복원한다. 참조와 재무 사실은 같은 트랜잭션으로 저장하고 재조회로 기존 행을 변환하지 않는다. 배치의 참조·공통 출처·보수적 메타데이터 추정량이 원래 JSON보다 작을 때만 공유한다. 이는 물리적 PostgreSQL 용량 절감 보장이 아니다.

`MARKET_SHARED_FINANCIAL_TRACES=0`이 기본값이며 기존 시계열 flag로 켜지지 않는다. 읽기 버전을 먼저 배포하고, 별도 활성화 도구에서 실제 API·작업자 이미지 및 명시한 이전 이미지의 읽기 지원을 확인한 뒤 영구 저장 형식 요구를 추가한다. 그 후 common 환경변수만 켜고 작업자만 재시작한다. 실패하면 설정을 되돌리되 영구 읽기 요구는 제거하지 않는다. 신규 실제 수집과 읽기 검사는 활성화 성공과 별도이며 도구가 모의 사실을 삽입하지 않는다. 기존 전체 재무의 변환·삭제·재투자 기능 추가는 범위에 포함하지 않는다. 실제 표본과 SQLite·격리 PostgreSQL 검증은 `work/financial-traces/RESULT.md`에 기록한다.


## 2026-10-10: 변경 없는 비교기 현금흐름과 원 공개 시점

OCF 누적자료가 다음 보고서에 같은 값으로 재공시되었다는 이유만으로 당시 단독 분기 차감을 막지 않는다. 해당 시점 전에 존재했던 원 입력 이후 모든 버전의 금액·가중평균 주식 수·기간·단위·차원·귀속·공식·검증된 분할 기준이 일치할 때만 원 사실 ID와 공개일로 계산한다. 실제 정정과 변경 후 원값 복귀는 계속 차단하며 EPS 등 다른 지표에는 예외를 확대하지 않는다. 계산 v4·투영 14로 기존 읽기 캐시를 갱신하고 원문과 수정 이력을 보존한다. 음수 실제 OCF는 표에 유지하되 양수 배수 비교선을 만들지 않는다. 대표 세 기업의 공개 대조와 한계는 `docs/releases/20261010-v112/ACCEPTANCE.md`에 기록한다.

## 2026-10-10: 주식 기준 검토의 원자 저장과 비교기 입력

자동 주식 기준 연장은 읽은 payload와 검토 인덱스 revision을 DB 트랜잭션 안에서 다시 대조하고, 이전 이력과 새 기준을 함께 저장한다. 늦은 자동 결과가 그사이 등록된 수동 검토를 덮어쓰지 않도록 한다. SQLite는 즉시 쓰기 트랜잭션, PostgreSQL은 정렬된 행 잠금과 조건부 갱신을 사용한다. 알림은 commit 이후에만 발행한다.

삼성전자 2026년 반기보고서의 2025년 비교기 보통주 수는 정확한 연결 주석·기간·차원·단위와 공시 해시를 대조한 한 건의 `fact_unit_reviews`로 연결한다. 실제 주식 단위가 바뀌지 않으므로 기존 basis ID·원문·공개일·분할 이력은 유지하고 snapshot revision으로 분석을 갱신한다. 도구의 기본 동작은 검토이며, 적용에는 현재 입력 3개 해시와 writer 중단 확인을 요구한다. 합성 자료는 운영 검토를 승인할 수 없다. 코드·로컬 검증과 운영 적용은 [v113 기록](docs/releases/20261010-v113/ACCEPTANCE.md)에서 구분한다.

## 2026-10-10: 원본 저장의 원자 공개와 중복 백업 보존 기준

새 원본·메타데이터는 같은 디렉터리의 임시 파일에 쓰고 파일 fsync를 마친 뒤 create-only 하드링크로 공개한다. 기존 파일과 먼저 도착한 수집 기록은 덮어쓰지 않는다. POSIX에서는 새 이름·교체·삭제 이후 디렉터리도 fsync한다. 압축 자료와 이전 메타데이터의 영속화를 확인하기 전에는 중복 원문을 삭제하지 않는다. Windows에서는 디렉터리 fsync를 지원하는 경로로 표시하지 않는다. 기존 0바이트 메타데이터의 내용을 추정해 채우는 복구는 별도 작업이며, 이번 저장 수정의 완료 범위에 포함하지 않는다.

VPS의 같은 날 중복 백업 한 개를 정리할 때는 원본과 더 최신 archive의 전체 해시·완결 manifest, 정확한 원본의 외부 암호화 사본·실제 복원 증거, 활성 사용 여부를 대조한다. 삭제 전 intent와 외부 증거를 파일·디렉터리 양쪽에 영속화하며, 새 백업과 다른 날짜 복구점·원래 manifest는 남긴다. 이 수동 도구는 전체 보존 정책을 완화하거나 자동 정리를 켜지 않는다. 운영 반영·실제 회수량은 버전별 검증 기록에서 따로 보고한다.

## 2026-10-10: 시장 목록과 뉴스 코드의 로딩 분리

뉴스 컴포넌트의 지연 JavaScript가 대시보드 전체의 초기 commit을 막지 않게 자체 로딩 경계를 둔다. 목록 조회와 표시가 먼저 진행되고 뉴스는 준비되면 해당 자리에 표시한다. 뉴스 청크를 보류한 회귀에서 수정 전 목록 요청 0건, 수정 후 목록 조회·표시와 뉴스 복구를 대조한다. 검색 색인은 최초 목록에서 받지 않고 검색 의도 후 기존 공유 loader로 불러온다. 이 검사는 공개 인터넷 지연 개선률이나 p95 통과를 대신하지 않는다.

## 2026-10-10: 비교 날짜에 맞는 자동 실적 기준

자동 TTM/연간 선택은 지표·주식 종류·분할 기준뿐 아니라 실제 관측 가격 날짜와 공시 적용일·검토 기간의 교집합을 사용한다. 같은 날짜 적용 함수를 선택과 비교선 생성에 사용한다. 명시적 TTM 요청은 연간으로 바꾸지 않고, 음수 TTM을 과거 양수 연간으로 대체하지 않는다. 값·공개일·주식 기준 검토 구간은 수정하지 않는다.

지표 목록은 가격 메타데이터만 읽는 경로를 유지한다. 메타데이터로 확실히 불가능한 구간만 제외하고 거래 날짜가 미확인인 경우 이를 별도 상태로 둔다. 공시값 존재와 메뉴 접근을 유지하되 실제 비교선 가능 여부는 가격을 읽은 분석 응답이 최종 판단한다. 산식 버전과 응답 캐시 버전을 함께 갱신한다.

## 2026-10-10: 날짜가 지난 복원본과 이후 변경 자료 분리

완료된 독립 복원본이 있어도 이후 운영 자료를 삭제할 근거가 되지 않는다. 추가·정정·삭제는 원출처를 포함한 전체 행의 결정론적 비교로 추출하고, 기준 복원·원본 백업·현재 스냅샷의 파일 해시와 생성 시각을 연결한다. 부분 파일은 완료 아카이브로 취급하지 않으며 원출처 삭제·변경은 거절한다. 압축 참조를 펼치기 전에 크기 한도를 확인한다.

`delta_archive`는 전체 검증된 두 스냅샷의 변경 파일 생성과 독립 적용 인터페이스까지다. 현재 운영 쓰기 차단, 실제 PostgreSQL 적용 대상의 소유권·체크포인트·디스크 검사, 전환 허가는 별도 조건이다. 이 모듈의 완료 파일이나 SQLite 시험 통과를 운영 원본 삭제 허가로 사용할 수 없다. 기존 복원 실행 중에는 그 코드와 데이터 디렉터리를 변경하지 않는다.

## 2026-10-10: 별도 로컬 PostgreSQL 후보에만 변경 자료 적용

`pg_delta_sink`는 완료된 복원본과 분리된 로컬 후보 DB만 허용한다. 접속 주소·디렉터리·클러스터 식별자·DB OID·소유자·정확한 두 테이블을 대조하고 체크포인트가 있으면 거절한다. 전체 기준 자료 검증과 변경 전 원출처 대조, 최종 전체 해시 검증을 한 트랜잭션에 묶는다. 시작 응답 유실을 포함해 실패 시 롤백 또는 연결 종료하며, COMMIT 결과가 불명확하면 자동 재시도하지 않는다. 공간·시간 한도는 적용 전후와 처리 중에 검사한다. 실제 운영 변경분 추출·후보 복제·전환은 별도이며 이 결과로 운영 자료를 삭제하지 않는다.

## 2026-10-10: 검색 색인 재생성과 공개 API 연결 준비

식별 검색 색인의 응답 캐시가 비어 있어도 같은 revision의 universe를 이미 읽었다면 기존 공유 입력을 재사용한다. 색인 생성 뒤 revision 대조는 유지한다. revision이 없으면 이전 공유 입력을 사용하지 않고 직접 조회하며, 초기 빈 DB의 200 응답을 유지하되 no-store로 반환한다. 일반 검색·상세가 요청마다 전체 universe를 읽는다는 가정은 사용하지 않는다.

정적 HTML에는 기존 공개 API 원점의 anonymous preconnect 하나만 둔다. 실제 요청의 credentials=omit과 맞추고, 서버용 URL·비밀 환경변수·다른 호스트를 연결 대상으로 사용하지 않는다. 캐시 정책·가격 갱신·사용자 저장값은 변경하지 않는다. 전체 서비스의 성능 목표 통과 여부는 공개 부하 측정으로 별도 판단한다.


## 2026-10-10: 승인된 저장 예산과 API별 점검 격리

MARKET_STORAGE_PROFILE은 고정 정책으로 선택한다. 미설정/default는 기존 90% 중단·80% 미만 재개·768MiB 여유를 보존한다. 사용자 승인으로 high-utilization을 운영에 적용하며 97% 또는 3GiB 미만에서 중단하고, 95% 미만·4GiB 이상 및 쓰기/fsync 성공 후 재개한다. 기록된 정책과 임계값을 함께 관측한다. 이는 단일 작업이나 다른 서비스의 급증을 예약하는 장치는 아니며 ENOSPC까지 채우는 정책이 아니다. 정상 자료·원본·다른 서비스는 정리하지 않는다.

OpenDART 800 점검 응답은 원천과 경로별 공유 재시도에 기록한다. XBRL/공시 원문 점검이 재무 JSON·공개 주석을 함께 막지 않게 하며 020과 일일 예산은 계정 전체에 유지한다. query/key를 재시도 식별자에 저장하지 않는다. 기존 범위 불명 재시도는 만료까지 보수적으로 유지하며, 원문과 이전 정상 자료를 보존한다.

실시간 수신 증거는 providers.trace가 실제로 만드는 source_document_id의 64자리 원문 해시를 읽는다. legacy sha256가 함께 있으면 두 값이 일치해야 한다. 원문·topic·종목·시각의 실제 대조와 브라우저 반영은 별도 검증이며, 구독 승인·heartbeat·fixture는 장중 수신 완료로 승격하지 않는다.

## 2026-10-10: 공유 작업자 실시간 검사와 제한된 Edge 요청 병합

실시간 운영 검사는 기존 작업자의 정확한 snapshot 키만 두 번 읽는다. 추가 토스 연결·구독·스냅샷 발급·DB 쓰기를 하지 않으며 원문 SHA, 종목, 체결/호가 유형, 시각, 공급자 장 일정 원문을 대조한다. 실제 수신, 강제 단절 복구, 브라우저 반영은 별도 판정한다. 휴장·오래된 값·heartbeat는 장중 수신으로 인정하지 않는다.

Cloudflare의 같은 isolate에 진행 중인 공개 검색·상세 요약 요청은 최대 32키, 응답당 64KiB, 본문 읽기 1.5초로 병합한다. 요청별 Response/stream을 공유하지 않고 최초 요청에서 완료된 바이트로 각 응답을 만든다. 원점·자료 버전·쿼리·표현/조건부 헤더를 구분하고 기존 1초 자료 신선도를 늘리지 않는다. 만료·오류·인증·호가·체결·WebSocket은 공유하지 않는다. 실제 Workerd 시험자료는 호출 중복 감소를 검증하며 공개 p95 통과와 구분한다.

공개 HTTP/2 측정은 모든 본문 수신 종료 시각을 먼저 기록한 뒤 압축 해제·JSON 검사를 수행한다. 한 TLS 세션의 100개 동시 요청과 100개 독립 연결을 구분하고, 캐시/병합 응답의 과거 Server-Timing을 해당 요청의 현재 앱 처리시간으로 합산하지 않는다. 측정 계약 v2와 이전 결과의 직접 속도 비교는 하지 않는다.

## 2026-10-10: 검토 기업의 EPS 주석 수집과 JYP 제한 구간

기존 주식 단위 검토가 있는 기업도 정확한 회사·접수번호·공개일의 미처리 EPS 주석을 수집 대상에 포함한다. 다섯 번 중 한 번의 우선 슬롯을 사용하고 신규 기업 순환을 유지한다. 완료 버전·자체 재시도·해당 원천 재시도를 확인하며 로컬 원문은 별도로 재검증한다. 선택 함수는 사실 등록이나 분할 승인을 하지 않는다.

JYP 035900의 공개 DART 원문 22개를 고정된 해시·표·회계기간·보통주 열로 검토한다. 신규 EPS 입력은 12개만 append하며 기존 반기 16개 원장 사실과 실제 XBRL 원문을 재검증해 중복 삽입하지 않는다. 단위 검토는 2025-01-01부터 2026-06-30까지이며 이후 현재가를 승인하지 않는다. 운영에서 계산한 TTM 두 구간의 공개일은 반기 비교자료가 발표된 2026-08-14 이후다. 공시·분할 검토 구간을 늘리기 전에는 이 결과만으로 현재 주가 비교선 완료라고 보고하지 않는다.

v127의 프런트엔드 소스는 v126과 같으므로 검증된 정적 파일을 다시 사용한다. 파일 목록·바이트·해시와 기존 빌드 시각을 유지하며 새 릴리스 조립 시각과 원본 manifest 해시를 따로 기록한다. 재사용을 새 컴파일이나 새 화면 검증으로 표현하지 않는다.

19:35 KST의 별도 후속 검토에서는 기존 JYP 주식 단위 이력을 보존하고 완전한 I 공시창을 2026-10-08까지 확인했다. 원재무 사실은 바꾸지 않았다. 두 공개 가치평가 API와 실제 브라우저에서 해당 창 안의 EPS × 15배 비교를 확인했으며, 공시 공개일 이전·검토창 밖을 승인하지 않는다. 초기 6월 한정 적용과 이후 10월 연장 검증을 구분한다.

## 2026-10-10: 100개 독립 연결 성능과 배당 근거 대조

공개 부하는 호스트별 독립 TLS 세션 100개·연결당 요청 1개로 검사한다. 기존 한 TLS의 100개 스트림 결과와 개선율을 계산하지 않는다. 실제 v127 2,000요청에서 검색300ms·상세요약500ms 목표는 모든 단계 미달이며 Cloudflare 경유가 더 느렸다. 원점 주소를 유지하고 관측 결과 없이 플랫폼 전체 이전을 실행하지 않는다. transport 실패의 status=0을 공급자의 HTTP 오류로 해석하지 않는다.

삼성 공식 IR의 배당 결정 3건은 운영 DART의 보통주·우선주 예정 배당 6건과 대조했다. 금액·권리일·예정일이 같으므로 중복 행사와 지급 확정 관측을 추가하지 않는다. 지급 예정, 실제 지급, 배당락은 각각 명시 근거를 요구한다. 부족한 근거는 새로 찾되 날짜가 지났다는 이유로 예정일을 확정일로 승격하지 않는다.

JYP 검토 바인딩은 검토한 원래 바이트의 SHA를 고정한다. Git의 Windows→Linux 개행 변환으로 검토 파일이 바뀌는 것을 `.gitattributes`의 해당 한 파일 `-text`로 막는다. 원문·금액·기간·승인 조건·기존 SHA는 변경하지 않으며 저장소 전체의 개행 규칙도 바꾸지 않는다. 검토 파일의 Git blob과 로컬 바이트가 같은지 확인하고 실제 Linux CI checkout에서 검토 회귀를 다시 실행한다.


## 2026-10-11: 주식 장보기와 읽기 중심의 시세 화면

표시 이름을 주식 장보기로 바꾸고 기존 주소·관심종목·설정 저장 키와 백업 형식은 유지한다. 기본 3개월 일봉 캔들 위에는 회사·가격·필수 설정만 둔다. OHLC 선택 값은 차트 아래로, 상하한가·초기화는 추가 설정으로 이동한다. 거래정지·VI 경고는 접지 않는다. 앱이 저장한 목록 위치를 복원하는 영역에서는 브라우저 scroll anchoring 및 자동 복원과 중복하지 않는다. 새로운 화면 기준은 일반 글씨 1440px 차트 시작 260px, 390px 380px, 시장 첫 화면 8행/3행이며 fixture 검사와 실제 공개 검증을 구분한다.

## 2026-10-11: 종목별 현재가와 호환 읽기

현재가는 기존 market_snapshots 안의 quote:<code> 행으로 갱신한다. SQLite BEGIN IMMEDIATE와 PostgreSQL 종목별 transaction advisory lock으로 최초 삽입과 부분 병합을 직렬화한다. 원래 quotes는 보존하고 전체 읽기는 원본·종목별 행·revision을 한 SELECT에서 합성한다. quote-book은 자료 시각이 아닌 내부 캐시 식별이다. 상세는 해당 종목 revision을 사용해 다른 종목의 시세 갱신으로 재생성되지 않게 한다.

같은 정상 시세를 처음 재수신할 때 종목별 행을 한 번 생성하되 원래 as_of·원본 근거는 바꾸지 않는다. GET은 전환 쓰기를 하지 않는다. 이후 동일 갱신은 no-op이며 빈 값·역전 가격·보완 원천이 기존 토스 가격을 덮지 않는다. 토스 배치는 최대 200종목을 원자적으로 병합한다. 구버전 전체 묶음 writer도 호환 Store에서는 종목별 병합으로 연결한다. 새 행을 읽지 못하는 순정 v130으로의 무조건 롤백은 금지하고 호환 Store를 포함한 롤백 산출물을 사용한다. 원본 DB의 과거 복원으로 롤백하지 않는다.

## 2026-10-11: 회사 태그와 검증된 문서 연결 분리

원천 뉴스 종목 태그는 후보로 보존한다. 공개 code/company/securities와 회사 필터는 review_status=verified, association_basis=document_issuer 또는 reviewed_document_subject, verified_security_codes가 있는 연결만 허용한다. 회사 필터는 SQL limit/count 이전에 적용한다. 후보는 candidate_securities/association_status로 구분하고 뉴스 공동 등장을 사업 관계로 노출하지 않는다. 원문·이전 LINKS·정정은 삭제하지 않는다.

공식 DART list 원문의 SHA·공식 URL·법인코드·종목코드·접수번호·제목·날짜를 저장된 법인 사전과 대조하는 경우 공시 발행회사 연결을 확정할 수 있다. 이는 사업 관계 추론과 다르다. 회당 최대 4종목×20행으로 체크포인트를 유지하고 디스크 80% 이상에서는 새 공시 색인을 실행하지 않는다. research 색인 버전에는 associations-v2를 포함한다. 본문 의미 검색이나 50문항 품질 통과를 이 변경만으로 주장하지 않는다.


## 2026-10-11: 후보 우선 검색과 기업별 조회 버전

단순 검색은 전체 시장의 가벼운 식별·정렬 필드에서 후보를 선택한 뒤 최대 30개만 상세 결합한다. 기존 거래대금 순서와 total을 보존한다. 기업 자료 투영은 원본 세대와 일치할 때만 사용하며 이전 writer 변경은 기존 경로로 복구한다. 상세는 종목별 기업·시세 버전으로 격리하고 가격 TTL을 늘리지 않는다.

## 2026-10-11: 복원 증거와 기업 자료 최초 검토

복원 처리 수, DB 반영 수, 검증 경계는 별개다. 작업 경로 증가량과 드라이브 전체 여유 감소를 따로 기록하되 최소 여유 공간은 계속 절대 중단 조건으로 사용한다. 직접 스트리밍 백업은 VPS 중복 덤프를 없애지만 덤프 목록·SHA 검사를 전체 복원으로 세지 않는다.

최초 주식 단위는 명시적으로 검토한 회사·보통주 무분할 구간·원문 해시·실제 입력 사실을 재대조한 대기열로만 등록한다. 분할·우선주 자동 승격은 제외한다. 배당 금액과 각 날짜의 확보·오류·충돌 상태는 독립적으로 표시한다. 예정 지급일의 경과는 실제 지급 근거가 아니다.

토스 요청 공정성은 같은 API 그룹에서 최신·사용자·과거 작업이 경합하는 동안 적용한다. 유휴·인증·용량 중단에서 예약 크레딧을 누적하지 않으며 공급자 한도와 Retry-After가 우선한다.
