# LUXON Investment Terminal — Claude Design 실행 마스터 프롬프트

상태: active implementation contract
대상: Claude Design / Claude Code / frontend implementer
주요 수정 범위: `apps/web/**`
검증 산출물: 루트 `design-qa.md`
기준일: 2026-08-16

## 이 문서의 용도

이 문서는 LUXON 프론트엔드를 단순히 "금융 대시보드처럼" 꾸미는 요청서가
아니다. Claude가 현재 저장소를 직접 읽고, 실제 API 계약과 기존 동작을
보존하면서, 다음 두 레퍼런스의 장점을 하나의 제품 흐름으로 구현하도록 하는
실행 계약이다.

- FAST Graphs에 가까운 역사적 가치평가 워크플로, 정보 위계, 차트 문법,
  조작 밀도
- FnGuide Company Guide에 가까운 한국 기업 Snapshot, Financials,
  Consensus, Peers 정보 구조

LUXON은 독자 제품이다. 레퍼런스의 상표, 로고, 문구, 코드, 이미지, 아이콘,
스크린샷, 비공개 데이터, 고유한 장식 요소를 복제하지 않는다. 목표는
**분석 과업과 정보 구조의 높은 동등성**이지, 제3자 제품의 트레이드 드레스를
복사하는 것이 아니다.

아래 `BEGIN PROMPT`부터 `END PROMPT`까지를 Claude Design 또는 Claude Code에
그대로 전달한다. 저장소 접근이 가능한 환경에서는 별도 요약 없이 이 문서를
읽고 실행하게 한다.

---

# BEGIN PROMPT

## 0. 역할

당신은 LUXON Investment Terminal의 수석 제품 디자이너이자 시니어
프론트엔드 엔지니어다. 사용자가 원하는 것은 컨셉 이미지나 기획서만이 아니라,
현재 Next.js 애플리케이션 안에서 실제로 작동하고 검증 가능한 UI/UX다.

다음 네 역할을 동시에 수행하라.

1. 공개 레퍼런스를 분석하는 제품 디자이너
2. 고밀도 금융 정보 화면을 설계하는 데이터 UX 디자이너
3. 기존 React/Next.js 코드를 안전하게 개선하는 프론트엔드 엔지니어
4. 숫자 출처와 결측 상태를 끝까지 드러내는 금융 데이터 감사자

계획만 제안하고 멈추지 마라. 저장소를 읽고, 구현하고, 브라우저에서 확인하고,
테스트하고, 차이를 고친 뒤 결과를 보고하라. 결과를 크게 바꾸는 정보가 실제로
없을 때만 질문하고, 이미 저장소와 이 프롬프트에 있는 내용은 다시 묻지 마라.

## 1. 최종 제품 결과

LUXON은 공개 저장소에서 개발되지만, 초기 실행 형태는 로컬 Windows + Docker
Compose 기반의 개인용 단일 사용자 투자 리서치 터미널이다. 공개 저장소와 공개
서비스는 같은 뜻이 아니다. 모든 추적 파일은 누구나 읽을 수 있다고 가정하고
비밀키, 개인 포트폴리오, 사용자 이메일, 인증 토큰, 라이선스 데이터, 로컬 경로,
세션 기록을 코드·fixture·문서·스크린샷에 넣지 마라.

완성된 경험에서 사용자는 다음 흐름을 끊김 없이 수행할 수 있어야 한다.

1. 한국 또는 지원되는 해외 종목을 검색한다.
2. 회사 헤더에서 가격, 시장, 통화, 데이터 상태와 최신 기준일을 확인한다.
3. Historical Graph에서 가격과 펀더멘털, 공정가치, 정상 멀티플을 비교한다.
4. 기간·지표·예측 조건을 바꾸되 계산 근거를 잃지 않는다.
5. Snapshot과 Financials에서 기업 실적과 재무 흐름을 촘촘하게 확인한다.
6. Consensus와 Peers가 실제 출처로 존재할 때만 이를 비교한다.
7. Forecast에서 외부 컨센서스, 사용자 가정, AI 검토 메모를 명확히 분리한다.
8. 화면의 숫자·점·선·표 셀을 눌러 Fact Audit에서 출처와 공식을 확인한다.
9. Screener, Watchlist, Portfolio로 분석 대상을 다시 찾고 추적한다.
10. System에서 공급자 설정, 수집 상태, 최신성, 누락 원인을 진단한다.

핵심 성공 기준은 "FAST Graphs처럼 보이는 화면" 한 장이 아니라,
**검색 → 역사적 가치평가 → 미래 시나리오 → 근거 감사**라는 핵심 루프가
실제로 작동하는 것이다.

## 2. 절대 규칙

### 2.1 금융 숫자 규칙

- LLM은 금융 숫자를 생성하지 않는다.
- 프론트엔드는 가치평가, CAGR, 목표가격, 배당수익, 품질점수, 피어 순위를
  재계산하지 않는다.
- 화면 값은 1차 공시, 검증된 API, 운영자가 제공한 검증 CSV, 사용자 입력,
  또는 백엔드의 결정론적 공식에서만 온다.
- `null`, 누락, 파싱 실패, 권한 없음은 `0`으로 바꾸지 않는다.
- 데이터가 없으면 빈칸을 숨기지 말고 정확한 unavailable state를 표시한다.
- 실제 데이터 모드에서 fixture를 자동 대체하지 않는다.
- `fixture_non_production`은 시각 회귀와 개발 확인 전용이다. 실제 리서치,
  운영 증거, 학습 데이터, 투자 판단 근거로 승격하지 않는다.
- AI는 이미 존재하는 숫자를 설명하거나 위험·가정을 서술할 수 있지만, 숫자와
  순위를 만들거나 수정할 수 없다.

### 2.2 소스 추적 규칙

- 저장된 모든 값에는 `source_trace`가 필요하다.
- point-in-time 값에는 `source_trace.available_at`가 필요하다.
- 파생 값에는 `formula`와 `input_fact_ids`가 필요하다.
- 화면에 표시되는 모든 금융 숫자는 Fact Audit으로 이동할 수 있어야 한다.
- 출처 링크가 없으면 URL을 추측하지 마라.
- quality 상태나 missing 이유를 색상만으로 표현하지 마라.
- `source_trace`가 없는 숫자를 보기 좋게 만들기 위해 임시 값을 넣지 마라.

### 2.3 지식재산과 브랜드 규칙

- FAST Graphs와 FnGuide의 로고, 상표, 제품명, 고유 문구, 이미지, 스크린샷,
  proprietary asset, 비공개 DOM/CSS/JS, 유료 데이터 구조를 제품에 넣지 마라.
- 공개 문서에서 확인 가능한 분석 과업, 내비게이션 범주, 차트의 도메인 문법,
  정보 밀도만 참고하라.
- FAST Graphs 캡처를 배경 이미지로 쓰거나 픽셀 트레이싱하지 마라.
- FnGuide를 직접 스크래핑하거나 엔드포인트·응답 형식을 추측하지 마라.
- FAST Graphs 또는 FnGuide의 인증 세션을 자동화하거나 보호된 화면의 DOM,
  스크린샷, 자산을 수집하지 마라. 공개 문서만 수동으로 검토하라.
- LUXON의 이름, 워드마크, 카피, 컴포넌트, 코드, 토큰, 데이터 모델을 유지하라.
- 분석 순서와 조작 위치는 레퍼런스에 매우 가깝게 만들 수 있지만, 제품 쉘과
  시각 정체성은 LUXON 고유여야 한다.

### 2.4 작업 범위 규칙

허용되는 기본 수정 범위:

- `apps/web/**`
- UI 검증을 위한 루트 `design-qa.md`
- 꼭 필요한 경우 이 핸드오프 문서의 사실 오류 수정

금지되는 기본 수정 범위:

- 백엔드 스키마, 공식, 데이터 정규화, 데이터베이스 테이블
- `services/api/**`, `packages/core/**`, connector 계산 로직
- 실제 공급자 응답, 컨센서스 숫자, peer membership 생성
- 인증·배포·GitHub 설정 변경
- 커밋, 푸시, PR 생성

프론트엔드에 필요한 필드가 실제 계약에 없으면 백엔드를 임의 수정하지 말고,
정확한 contract gap을 `design-qa.md`와 최종 보고에 기록하라. 해당 UI는
disabled, unavailable, 또는 source-required 상태로 구현하라.

## 3. 작업 시작 전에 반드시 읽을 파일

저장소 루트에서 다음 순서로 읽어라. 파일 이름만 보고 추정하지 마라.

1. `AGENTS.md`
2. `apps/web/AGENTS.md`
3. `DECISIONS.md`
4. `README.md`
5. 이 파일 `docs/CLAUDE_DESIGN_HANDOFF.md`
6. `docs/CLAUDE_DESIGN_QA_CHECKLIST.md` — 모든 P0/P1 항목은 blocking
7. `apps/web/package.json`
8. `apps/web/app/layout.tsx`
9. `apps/web/app/page.tsx`
10. `apps/web/app/styles.css`
11. `apps/web/lib/terminal-config.ts`
12. `apps/web/lib/terminal-workflow.ts`
13. `apps/web/lib/terminal-types.ts`
14. `apps/web/lib/terminal-source-gate.ts`
15. `apps/web/lib/terminal-source-normalizers.ts`
16. `apps/web/lib/audit-utils.ts`
17. `apps/web/components/*.tsx`
18. `apps/web/app/terminal/page.tsx`
19. `apps/web/app/company/[id]/page.tsx`
20. `apps/web/app/company/[id]/[view]/page.tsx`
21. `apps/web/app/screener/page.tsx`
22. `apps/web/app/portfolio/page.tsx`
23. `apps/web/app/system/page.tsx`
24. `services/api/contracts.py` — 읽기 전용
25. `services/api/research_contracts.py` — 읽기 전용
26. `services/api/main.py`의 실제 라우트 선언 — 읽기 전용
27. `apps/web/tests/valuation.spec.ts`

또한 다음을 먼저 확인하라.

- `git status --short`로 기존 사용자 변경을 파악한다.
- 현재 브랜치와 원격을 읽기 전용으로 확인한다.
- 현재 실행 스크립트와 테스트 명령은 `package.json`에서 확인한다.
- 현재 화면을 캡처하기 전 임의로 스타일을 바꾸지 않는다.
- 관련 없는 변경을 되돌리거나 덮어쓰지 않는다.

## 4. 레퍼런스 우선 작업

### 4.1 공개 레퍼런스

우선 확인할 공개 자료:

- FAST Graphs Historical Graph guide
  <https://docs.fastgraphs.com/en/articles/9419962-historical-graph>
- FAST Graphs Forecasting Charts User Guide
  <https://docs.fastgraphs.com/en/articles/13577168-forecasting-charts-user-guide>
- FnGuide Company Guide information architecture
  <https://wcomp.fnguide.com/Help/Guide?cmp_cd=0101N0>

인증된 제3자 계정에 로그인하거나 접근 제한을 우회하지 마라. 공개 문서만으로
확인할 수 없는 부분은 "미확인"으로 남겨라. 레퍼런스 페이지의 문구나 이미지를
제품 코드에 복사하지 마라.

### 4.2 필수 캡처와 비교 절차

1. 공개 레퍼런스의 Historical 상태를 캡처한다.
2. 공개 레퍼런스의 Forecasting 상태를 캡처한다.
3. 현재 LUXON Historical 상태를 동일 viewport로 캡처한다.
4. 현재 LUXON Snapshot, Financials, Forecast 상태도 캡처한다.
5. 레퍼런스와 구현 화면을 같은 크기의 하나의 비교 보드에 나란히 배치한다.
6. 정보 위계, 밀도, 정렬, plot 비중, control 위치, rail 폭, 타이포,
   border, radius, overflow, focus 상태를 비교한다.
7. 보이는 차이를 수정한 뒤 같은 상태로 다시 캡처한다.

제3자 reference capture와 비교 보드는 반드시 저장소 밖 운영체제 임시
디렉터리에만 둔다. `git add`, commit, 제품 asset 재사용을 금지한다. 저장소에는
공개 URL, viewport, 측정값, 관찰 결과와 독립 구현 판단만 기록한다. 이전 실행의
임시 파일이 다른 환경에 없으면 공개 reference를 다시 캡처한다. 임시 파일이
없다는 사실만으로 디자인 작업을 blocked 처리하지 마라.

1280×720 캡처가 레퍼런스에 존재하면 먼저 그 크기를 사용하고, 제품 검증에는
1440×900, 1024×768, 390×844를 추가하라. 단순히 각각의 스크린샷을 봤다고
비교 완료로 간주하지 마라. 동일 viewport와 동일 UI 상태를 한 비교 입력에서
판단하라.

### 4.3 비교할 항목

- company header가 차트보다 과도하게 큰가
- primary tabs가 한눈에 읽히는가
- metric/period/chart settings가 차트 직전에 붙어 있는가
- main chart가 첫 화면의 주인공인가
- right facts rail이 차트와 동시에 보이는가
- 회사 정보와 출처 상태가 분석 흐름을 방해하지 않는가
- chart legend와 line semantics가 즉시 이해되는가
- 데이터가 없을 때 빈 카드 대신 다음 행동이 명확한가
- 장식용 카드와 과도한 여백이 정보 밀도를 낮추지 않는가
- LUXON 브랜드가 레퍼런스와 분명히 구분되는가

## 5. 사용자와 핵심 문제

주 사용자는 장중 매매 터미널이 아니라, 기업의 장기 실적과 가치평가를 반복해서
확인하려는 개인 투자자다. 사용자는 여러 사이트, 엑셀, 공시, 차트 사이를
오가며 다음 문제를 겪는다.

- 가격 차트와 펀더멘털 추세가 분리되어 있다.
- 현재 멀티플이 역사적 정상 수준과 얼마나 다른지 빠르게 보기 어렵다.
- 실적 actual과 estimate가 섞여 보인다.
- 컨센서스의 출처와 기준일을 잃기 쉽다.
- peer가 왜 peer인지 설명되지 않는다.
- 보기 좋은 숫자가 실제 공시인지, 재구성인지, 사용자 가정인지 알기 어렵다.
- 한국 종목 데이터가 없을 때 해외 fixture가 조용히 대신 노출될 위험이 있다.

따라서 LUXON의 디자인 차별점은 AI 장식이 아니라 다음 세 가지다.

1. 가격·펀더멘털·가치평가의 동시 비교
2. actual·estimate·manual·AI explanation의 분리
3. 화면 값에서 원출처와 공식까지 한 번에 내려가는 auditability

## 6. 제품 정보 구조

### 6.1 canonical 전역 구조

다음 구조를 유일한 target IA로 사용한다.

1. **Global shell**: LUXON wordmark, 검색, 환경/source 상태, 인증 사용자
2. **Stable primary product navigation**: Terminal, Screener, Portfolio, System
3. **Stable company tabs**: Graph, Snapshot, Financials, Forecast, Consensus,
   Peers, Performance, More
4. **More menu**: Analyst Scorecard, Fun Graphs, Fiscal Fitness, Research Report,
   Health Check, Use of Cash, Watchlist, Data Audit 및 나머지 저빈도 화면

`Graph`는 기존 `Historical`, `Snapshot`은 기존 `Summary`, `Forecast`는 기존
`Forecasting` workspace 이름과 호환된다. 회사 탭은 한 개의 안정적인 줄을
유지하고, 활성 탭이 다른 줄이나 메뉴 위치로 이동하지 않게 한다. 전역 기능과
회사별 기능을 섞지 말고, 모든 More 항목의 키보드 접근, 현재 위치, deep link를
보존한다.

### 6.2 현재 라우팅 계약

현재 query-state 터미널은 legacy compatibility layer다. 최종 canonical route는
실제 company shell이며 기존 URL을 깨지 않는 범위에서 단계적으로 이관한다.

- `/terminal`은 검색과 최근 리서치 재개 화면
- `/company/[id]/[view]`는 실제 회사 workspace shell
- `/terminal?ticker={ticker}&tab={workspace}`는 기존 alias로 계속 해석
- 초기 default/alias 정규화만 URL replace 사용
- 사용자의 ticker, tab, view 변경은 history를 push하여 Back/Forward로 재생
- 지원하지 않는 ticker는 값을 보여주지 않고 unsupported security state 표시
- 브라우저 `popstate`로 복원

현재 stable alias:

| URL | 연결되는 화면 |
| --- | --- |
| `/terminal` | Search / resume |
| `/company/[id]` | Graph |
| `/company/[id]/graph` | Graph |
| `/company/[id]/historical` | Graph legacy alias |
| `/company/[id]/snapshot` | Snapshot |
| `/company/[id]/performance` | Performance |
| `/company/[id]/forecast` | Forecast |
| `/company/[id]/forecasting` | Forecast legacy alias |
| `/company/[id]/financials` | Financials |
| `/company/[id]/consensus` | Consensus |
| `/company/[id]/peers` | Peers |
| `/company/[id]/audit` | Data Audit |
| `/screener` | Screener |
| `/portfolio` | Portfolio |
| `/system` | System |

현재 redirect shell은 임시 호환 계층일 뿐 최종 합격 상태가 아니다. 각 company
route는 서버에서 의미 있는 실제 화면 shell을 제공하고 정확한 ticker/workspace,
새로고침, deep link, back/forward를 보존해야 한다. 기존 query URL도 호환하되
redirect-only 구조를 단계적으로 제거하라.

### 6.3 현재 구현에서 반드시 해소할 P0 게이트

`docs/CLAUDE_DESIGN_QA_CHECKLIST.md`의 상세 재현 조건을 기준으로 다음 항목을
모두 닫기 전에는 디자인 완료라고 보고하지 마라.

1. `fixture_non_production`과 green `source-backed`/live 표현이 한 화면에
   동시에 존재하지 않도록 상태 불변식을 중앙화한다.
2. 주요 영역의 DOM 순서와 시각 순서를 일치시킨다. CSS `order`로 Ask 허브,
   차트, 회사 정보 같은 큰 영역을 재배치하지 않는다.
3. 활성 workspace가 두 줄 사이를 이동하지 않는 안정적인 단일 내비게이션
   모델을 만든다. 저빈도 화면은 명시적 overflow/More 구조로 보존한다.
4. 20개 이상 endpoint를 하나의 `Promise.all` 실패 경계에 묶지 않는다.
   workspace별 query, loading, empty, retry, stale/error state를 독립시킨다.
5. `/company/[id]/[view]`를 redirect-only가 아닌 실제 route shell로 만든다.
6. Historical 전용 metric/period/chart controls는 다른 workspace에 반복
   노출하지 않는다. Forecasting에는 해당 화면에 필요한 controls만 둔다.
7. 동일 기능의 chart settings를 band와 drawer에 중복 렌더링하지 않고 한 개의
   canonical surface로 통합한다.
8. 1440, 1024, 390px에서 page-level horizontal overflow를 0으로 만든다.

## 7. 전체 화면 골격

### 7.1 Desktop Historical 기준 골격

다음 골격을 목표로 하되 실제 레퍼런스와 현재 코드를 측정해 세부 크기를
조정하라.

```text
┌──────┬──────────────────────────────────────────────────────────────────────┐
│ rail │ LUXON | workflow | search | data mode | owner | status             │
├──────┼──────────────────────────────────────────────────────────────────────┤
│      │ Company / ticker / market / close / change / source freshness       │
│      ├──────────────────────────────────────────────────────────────────────┤
│      │ Graph Snapshot Financials Forecast Consensus Peers Performance More   │
│      ├──────────────────────────────────────────────────────────────────────┤
│      │ metric selector | period presets | custom range | chart settings    │
│      ├──────────────────────────────────────────────┬───────────────────────┤
│      │                                              │ current facts         │
│      │  historical valuation chart                 │ graph key             │
│      │  price + metric + value references          │ company information   │
│      │                                              │ source/quality        │
│      ├──────────────────────────────────────────────┴───────────────────────┤
│      │ annual high/low or selected period strip | range | performance      │
└──────┴──────────────────────────────────────────────────────────────────────┘
```

우선순위는 `chart > selected facts > controls > source context > secondary
content`다. Graph에서 AI Underwriter hero, 추천 문구, 긴 onboarding,
workspace map은 차트보다 위에 오면 안 된다. Ask/Underwrite hub는 별도 Home 또는
Underwrite route의 secondary task다. FnGuide형 Snapshot 안에 넣거나 Snapshot을
대체하지 마라.

### 7.2 크기와 밀도 원칙

- 현재 기준 left rail 72px, workspace horizontal padding 22px, top bar 약
  72px를 출발점으로 사용한다.
- 1440px에서 본문 차트와 facts rail이 동시에 보이고 페이지 전체 horizontal
  scroll이 없어야 한다.
- 1440px에서 chart/facts 비율은 plot 78–82%, rail 18–22%를 비교 출발점으로
  삼는다. 공개 reference와 동일 viewport에서 실제 폭을 측정해 조정하되 rail이
  plot을 압도하지 않고 핵심 facts는 읽을 수 있어야 한다.
- Historical의 company header, tabs, controls는 가능한 한 3~4개의 조밀한
  수평 band 안에 정리한다.
- 고밀도는 작은 글씨만 뜻하지 않는다. 정렬, 단위, 그룹, whitespace 리듬으로
  많은 정보를 빠르게 스캔할 수 있어야 한다.
- 카드마다 큰 제목·설명·그림자를 반복하지 마라.
- 장식용 gradient, glassmorphism, 과도한 radius, 떠 있는 마케팅 카드,
  의미 없는 아이콘을 사용하지 마라.

## 8. LUXON 시각 시스템

### 8.1 기존 토큰을 기준으로 사용

`apps/web/app/styles.css`의 현재 CSS variables가 시작점이다.

| 역할 | 현재 토큰/값 | 규칙 |
| --- | --- | --- |
| page background | `--bg: #f6f7f9` | 중립 배경 |
| surface | `--surface: #ffffff` | 주요 작업 표면 |
| secondary surface | `--surface-2: #f8fafc` | 보조 그룹 |
| line | `--line`, `--line-strong` | 1px 계층 구분 |
| text | `--text: #111827` | 본문/숫자 |
| muted | `--muted: #6b7280` | 보조 메타 |
| brand | `--brand: #6d5ef6` | LUXON 선택·focus에만 제한 사용 |
| price | `--chart-price: #14161a` | 가격선 |
| fundamental | `--chart-fundamental: #2e9e6b` | 실적 면적/선 |
| forecast | `--chart-forecast: #9bd8b8` | estimate 영역 |
| normal multiple | `--chart-normal: #2f6fed` | 정상 멀티플 |
| fair value | `--chart-fair: #f5912b` | 공정가치 |
| recession | `--chart-recession` | 경기침체 band |
| dividend | `--dividend-floor: #d6a300` | 배당 reference |

새로운 색상, spacing, radius, type scale이 반복되면 token으로 만든다. 한 번만
쓰는 magic number를 늘리지 마라. chart semantic colors와 product brand color를
혼동하지 마라.

### 8.2 타이포그래피

- 기존 Inter/system stack을 유지한다.
- 표와 금융 숫자는 `font-variant-numeric: tabular-nums`를 사용한다.
- company name, section title, row label, value, source metadata의 크기와 weight를
  명확히 분리한다.
- 본문 최소 가독성을 지키고 9~10px 텍스트는 짧은 보조 label에만 제한한다.
- uppercase는 짧은 상태·eyebrow에만 쓴다.
- 긴 설명보다 짧고 직접적인 label을 사용한다.

### 8.3 표면과 형태

- 기본은 평평한 surface + 1px border다.
- 주요 panel radius는 기존 `--terminal-radius`를 따르되 Historical의 고밀도
  band는 더 작은 radius를 기존 스타일 범위 안에서 쓸 수 있다.
- 그림자는 overlay, drawer, modal처럼 실제 elevation이 필요한 곳에만 쓴다.
- 활성 탭은 색상, border/indicator, weight 중 최소 두 가지로 구분한다.
- 상태 badge는 의미별 색뿐 아니라 명시적 text를 포함한다.

### 8.4 다크 모드

기존 `[data-theme="dark"]` 토큰을 깨지 마라. 다크 모드가 현재 주 과업을
방해하면 새 기능을 확대하지 말고, 변경한 컴포넌트가 기존 토큰으로 최소한
읽히는지만 검증한다. light mode가 P0 기준이다.

## 9. Company Header와 Navigation 상세 계약

### 9.1 Company Header

항상 다음을 한 눈에 제공한다.

- 회사명
- ticker + market
- currency와 latest close
- 변화율이 실제로 존재할 때만 변화율
- latest data 기준일 또는 source freshness
- `source_backed`, `partial`, `stale`, `fixture_non_production`, unavailable 상태
- portfolio action

Chart settings는 Company Header의 상시 action이 아니다. Graph에서만 하나의
canonical trigger를 제공하고, chart를 포함하는 Forecast가 별도 설정을 필요로
하면 해당 route 안에서 Graph 설정과 명확히 구분된 한 개의 trigger만 제공한다.

데이터가 없을 때 `source-backed` 같은 긍정 문구를 대체 표시하지 마라. 실제 값이
없으면 `—`와 구체적 reason을 사용한다.

### 9.2 Source gate

source-ready일 때 source gate가 큰 카드로 세로 공간을 소비하지 않게 compact
status line 또는 disclosure로 축약한다. `missing_source`, `missing_contract`,
`missing_key`처럼 화면을 차단해야 할 때만 명확한 gate panel을 보여준다.

KR 우선 종목에서 source trace가 없으면 다음 원칙을 지킨다.

- financial values blank
- source-required title
- 필요한 공급자 또는 ingestion action 표시
- fixture로 교체 금지
- 그래프 영역에도 "차트를 만들 수 없는 이유"를 표시

### 9.3 Tabs

회사별 primary 순서는 고정한다.

1. Graph
2. Snapshot
3. Financials
4. Forecast
5. Consensus
6. Peers
7. Performance
8. More

More는 Analyst Scorecard, Fun Graphs, Fiscal Fitness, Research Report, Health
Check, Use of Cash, Watchlist, Data Audit 같은 저빈도 회사 화면을 labelled menu로
제공한다. Screener, Portfolio, System은 회사 탭이 아니라 global navigation이다.
활성 More 화면은 More trigger와 menu 안에서 현재 위치를 동시에 표시한다. 좁은
화면에서도 탭을 두 줄로 재배치하거나 활성 탭을 다른 위치로 이동하지 마라.

## 10. Historical Graph — 최우선 P0 화면

### 10.1 사용자 목표

사용자는 한 화면에서 "기업이 벌어온 이익/현금흐름에 비해 가격이 언제 비쌌고
쌌는지, 현재는 역사적 정상 멀티플과 공정가치 대비 어디에 있는지"를 판단한다.

### 10.2 필수 화면 순서

1. compact company header
2. company analysis tabs
3. single compact control band
4. main chart + right evidence/facts rail
5. annual high/low, selection, return strip
6. chart 아래의 보조 설명/AI workflow

### 10.3 Control band

반드시 포함하거나 접근 가능해야 하는 것:

- metric selector
- period presets
- custom start/end year
- forecast mode
- forecast case
- forecast years 1Y–5Y
- normal multiple lookback
- user growth와 target multiple — custom mode에서만
- dividends toggle
- recession bands toggle
- current/custom valuation line toggles
- scenario line toggles
- saved layout 선택·저장
- chart settings expand/collapse

기본 닫힌 상태에서는 metric, period, custom date 진입, chart settings만 한 줄에
조밀하게 보인다. advanced controls는 settings를 열었을 때 논리 그룹으로 나타난다.
버튼과 select가 서로 겹치거나 페이지 horizontal overflow를 만들면 실패다.

### 10.4 차트 레이어 문법

다음 semantic layer를 유지한다.

| Layer | 표현 | 의미 |
| --- | --- | --- |
| fundamental actual | 진한 green area/line | source-backed reported metric |
| fundamental estimate | 더 옅은 green area/line | forecast/estimate, actual과 분리 |
| price | black/dark line | source-backed close price |
| fair value | orange line | backend deterministic fair-value reference |
| normal multiple | blue line | backend historical normal-multiple reference |
| current/custom valuation | 명확히 구분되는 reference line | 현재 또는 사용자 설정 기준 |
| dividend | gold/yellow reference | 배당 floor 또는 dividend-related layer |
| recession | translucent gray vertical bands | source-backed macro periods |
| portfolio trade | buy/sell marker | 실제 imported transaction에서만 |

다음 규칙을 지켜라.

- actual/forecast 경계를 배경, line treatment, label 중 두 가지 이상으로 구분한다.
- line legend에는 색뿐 아니라 이름, visible state, source/quality 진입점을 둔다.
- 서로 겹치는 선의 tooltip은 모든 값을 열거하고 각 audit target을 제공한다.
- hover만으로 중요한 정보를 숨기지 않는다. keyboard와 click selection이 가능해야
  한다.
- 선택 연도는 chart, annual strip, right rail, audit drawer에서 동기화한다.
- `source_trace`가 없는 layer는 그리지 않는다.
- empty chart에 sample curve를 그리지 않는다.

### 10.5 Right rail

desktop에서 차트와 동시에 보이며 다음 순서로 정리한다.

1. selected year/current facts
2. fair value와 price 차이
3. normal multiple
4. selected metric
5. total return 또는 selected-period result — API 값이 있을 때만
6. Graph Key
7. company information
8. source/quality summary

각 숫자와 legend row는 audit target이다. rail 안에서 긴 JSON을 바로 노출하지
말고, 핵심 source/method/quality를 보여준 뒤 drawer에서 전체 trace를 연다.

### 10.6 하단 strip

- annual high/low 또는 연도별 데이터
- actual/estimate 표시
- 선택 range
- 시작/종료 selection과 performance
- series visibility
- imported transaction markers

작은 화면에서는 별도 collapsible panel 또는 horizontal table container로 바꿀 수
있지만 값을 삭제하거나 의미를 바꾸지 마라.

### 10.7 Historical empty/error

- `loading`: 숫자가 들어간 skeleton 금지. 구조만 표시한다.
- `missing_source`: 필요한 source와 ingestion action을 보여준다.
- `partial`: 사용할 수 있는 연도만 표시하고 누락 연도 목록과 reason을 제공한다.
- `stale`: 마지막 실제 값을 timestamp와 함께 보이되 stale badge를 고정한다.
- `fixture_non_production`: 전체 chart surface에 지속적 비운영 표시를 둔다.
- `upstream_error`: 마지막 source-backed 값이 없으면 차트를 비우고 retry를 제공한다.

## 11. Snapshot — FnGuide형 기업 개요

### 11.1 사용자 목표

사용자는 그래프를 읽기 전에 회사의 규모, 수익성, 성장, 밸류에이션, 사업 설명,
데이터 품질을 촘촘하게 훑는다.

### 11.2 권장 섹션 순서

1. quote/company identity strip
2. 핵심 투자 지표
3. valuation preview
4. earnings/profitability/growth summary
5. balance-sheet and cash-flow health
6. business/company information
7. ownership/industry context — 계약에 있을 때만
8. source quality and audit summary

현재 `SummaryPanel`과 `SummaryValuationPreview`를 보존·개선하라. 이미 존재하는
EPS, growth, market cap, PER, PBR, ROE, ROIC, debt, dividend 같은 값만 사용하고
타입에 없는 지표를 만들지 마라.

### 11.3 Snapshot UI 규칙

- metric card 남발보다 4–6열 dense metric grid를 사용한다.
- 숫자는 단위와 기준일을 함께 표시한다.
- 값이 없는 셀은 `—` + reason/tooltip로 나타낸다.
- 핵심 metric click은 Fact Audit으로 연결한다.
- mini valuation preview는 Historical로 이동할 수 있어야 한다.
- 긴 company description이 핵심 숫자를 아래로 밀지 않게 disclosure를 사용한다.
- 한국어 사업 설명과 영문 UI가 섞이면 언어 계층을 명확히 한다.

## 12. Financials — 재무제표 워크스페이스

### 12.1 사용자 목표

사용자는 annual/quarterly/TTM과 reported/reconstructed를 혼동하지 않고 손익,
재무상태, 현금흐름의 추세를 비교한다.

### 12.2 필수 기능

- statement family: Income, Balance Sheet, Cash Flow, Ratios
- period basis: Annual, Quarterly, TTM
- reporting basis: Reported, Reconstructed
- display basis: absolute, per share, common size
- actual/estimate label
- currency/unit scale
- frozen row labels
- selected cell audit
- compact trend visualization

현재 계약이 annual reported만 제공하면 다른 control을 활성화하여 client에서
가짜 변환하지 마라. control은 disabled 또는 unavailable로 유지하고 필요한
contract를 설명한다. `FinancialsPanel`의 현재 mode contract와 테스트를 먼저
확인한다.

### 12.3 Table 규칙

- 숫자는 우측 정렬, tabular numerals, 동일 decimal policy를 사용한다.
- 연도 열은 actual과 estimate를 시각·텍스트로 구분한다.
- 음수는 minus 기호와 충분한 대비로 표현한다.
- 단위가 바뀌면 table header에 명시한다.
- horizontal scroll은 table container 내부에서만 허용한다.
- 첫 열과 가능한 경우 header를 sticky 처리한다.
- keyboard로 셀과 audit action에 접근 가능해야 한다.
- mobile은 모든 열을 카드로 복제하지 말고, 핵심 요약 + 선택 가능한 table
  viewport를 제공한다.

## 13. Forecasting — 결정론적 미래 시나리오

### 13.1 사용자 목표

사용자는 1–5년 EPS/metric 가정과 목표 멀티플을 바꾸고, 결과 수익률과 위험이
어떻게 달라지는지 확인한다.

### 13.2 근거 lane을 절대 섞지 말 것

| Lane | 의미 | 숫자 생성 주체 |
| --- | --- | --- |
| External consensus | 외부 시점별 추정치 | 검증 공급자/CSV |
| Manual assumption | 사용자가 직접 입력 | 사용자 |
| Deterministic formula | CAGR, target, return components | 백엔드 공식 |
| AI review | 가정과 위험에 대한 문장 | LLM, 숫자 생성 금지 |

각 lane에 source, method, quality, timestamp를 표시한다. AI review를
"AI forecast"로 부르거나 숫자선처럼 그리지 마라.

### 13.3 Forecast 화면 구조

1. mode/case/year controls
2. base facts와 selected source
3. low/median/high 또는 manual scenario lines
4. target multiple/target price/return components — API 값만
5. estimate coverage/analyst scorecard link
6. assumption risk and invalidation notes
7. Fact Audit links

control 변경 시 debounce와 request race protection을 유지한다. 진행 중인 이전
요청이 늦게 도착해 최신 선택을 덮어쓰면 실패다.

### 13.4 Consensus가 없을 때

- manual mode를 명시적으로 선택하게 한다.
- `missing_contract`를 숨기지 않는다.
- low/median/high를 임의 생성하지 않는다.
- "analyst consensus" 문구를 쓰지 않는다.
- AI가 컨센서스를 대신하게 하지 않는다.

## 14. Consensus — 출처가 있을 때만 표시

실제 endpoint:

- `GET /api/v1/companies/{company_id}/consensus`

현재 계약은 다음을 제공한다.

- company_id
- metric_key / metric_name
- forecast_year
- provider
- evidence_kind
- quality_status
- cases: low, median, high, current 중 실제 존재하는 case
- 각 case의 estimate EPS `FactValue`
- optional growth rate `FactValue`
- assumption_type: external_consensus 또는 manual_assumption

UI 요구:

- external/manual/mixed를 명시한다.
- 실제 case 수를 표시한다.
- 없는 case를 빈 숫자나 0으로 채우지 않는다.
- provider와 quality를 header에 둔다.
- 각 estimate와 growth는 Fact Audit 대상이다.
- revisions history는 현재 계약에 없으므로 차트를 만들지 않는다.
- revisions가 필요하면 contract gap으로 기록한다.
- `missing_contract`, `missing_source`, `partial`을 각각 다른 문구와 action으로
  처리한다.

## 15. Peers — business와 valuation을 분리

실제 endpoint:

- `GET /api/v1/companies/{company_id}/peers?kind=business|valuation`

현재 계약은 다음을 제공한다.

- company_id
- kind: business 또는 valuation
- peers
- peer company_id, name, relationship
- optional facts
- peer relationship source_trace

UI 요구:

- Business peers / Valuation peers segmented control을 유지한다.
- peer가 선정된 이유인 `relationship`을 이름 옆에 보여준다.
- facts가 비어 있으면 비교 숫자를 만들지 않는다.
- source trace가 없는 peer를 승인된 peer처럼 표시하지 않는다.
- sector label만으로 peer를 추론하지 않는다.
- peer ranking은 실제 계약이 없으면 만들지 않는다.
- 현재 회사와 peer의 단위·기간이 다르면 한 표에서 직접 비교하지 않는다.
- unavailable일 때 필요한 validated peer CSV contract를 설명한다.

## 16. Performance와 Analyst Scorecard

### 16.1 Performance

- 선택 기간의 price return, dividend return, total return을 API 결과로 표시한다.
- benchmark가 실제 계약에 있을 때만 비교한다.
- 시작/종료 연도와 선택이 Historical과 동기화되어야 한다.
- chart와 table 양쪽에서 동일한 값을 다른 방식으로 재계산하지 않는다.
- annualized/absolute return의 단위를 명시한다.
- source-backed price/dividend row를 audit으로 연결한다.

### 16.2 Analyst Scorecard

- 과거 estimate와 actual의 비교 근거를 보여준다.
- coverage count, horizon, methodology를 명시한다.
- 정확도 score가 API에서 오지 않으면 계산하지 않는다.
- analyst 개인 평가나 추천 등급을 생성하지 않는다.
- missing estimate coverage는 명확한 empty state다.

## 17. Fun Graphs, Fiscal Fitness, Health Check, Use of Cash

이 화면은 P1이다. Historical P0를 먼저 완성한 뒤 다룬다.

### Fun Graphs

- metric 선택과 추세 비교를 지원한다.
- 같은 축에 단위가 다른 series를 무리하게 겹치지 않는다.
- 각 point는 source/audit으로 이동한다.

### Fiscal Fitness

- 수익성, 재무안정성, 현금흐름, 자본효율을 구분한다.
- composite score를 프론트에서 만들지 않는다.
- pass/fail만으로 원값과 기준을 숨기지 않는다.

### Health Check

- radar 또는 axis가 현재 계약에 있으면 사용한다.
- axis 의미와 기준을 text/table로도 제공한다.
- 색상만으로 좋음/나쁨을 표현하지 않는다.

### Use of Cash

- operating cash, capex, dividends, buybacks, debt, M&A 등 실제 row만 표시한다.
- sign convention을 명확히 한다.
- 기간과 통화를 header에 둔다.

## 18. Screener, Watchlist, Portfolio

### 18.1 Screener

지원되는 현재 filter contract를 우선 사용한다.

- max PER
- min ROE
- min EPS CAGR
- max debt-to-equity
- min market cap / min market cap USD
- relative discount
- ROE > ROIC 요구 여부

UI 요구:

- active filter를 compact badge로 표시한다.
- unavailable metric 때문에 제외된 종목 수 또는 coverage를 가능한 경우 표시한다.
- 결과 숫자를 client에서 임의 계산하지 않는다.
- sorting/filtering 기준과 단위를 표시한다.
- 행 선택 시 company deep link로 이동한다.
- zero results와 data-unavailable을 구분한다.

### 18.2 Watchlist

- ticker, note, source status, 주요 실제 지표만 표시한다.
- add/remove 성공·실패 state를 제공한다.
- optimistic update를 쓰면 실패 시 되돌린다.
- watchlist 항목이 있다고 source-backed company data가 있다는 뜻으로 표시하지
  않는다.

### 18.3 Portfolio

- import된 transaction만 buy/sell marker에 사용한다.
- quantity, price, date, currency, fees가 실제 contract에 있을 때만 계산/표시한다.
- sample CSV는 sample임을 명확히 한다.
- 실제 개인 포트폴리오나 이메일을 공개 저장소 fixture에 넣지 않는다.
- import preview, validation error, commit 단계가 구분되어야 한다.
- source trace와 user-input trace를 구분한다.

## 19. Data Audit — LUXON의 핵심 차별점

### 19.1 모든 숫자의 도착점

다음 click target은 Data Audit drawer 또는 workspace로 연결되어야 한다.

- Snapshot metric
- Historical chart point와 line legend
- right rail fact
- Financials cell
- Forecast assumption/result
- Consensus case
- Peer fact/relationship
- Performance result
- Screener result
- Portfolio transaction-derived 표시

### 19.2 기본 source_trace 필드

최소 storage-ready 필드:

- `source`
- `filing_id`
- `period`
- `available_at`
- `unit`
- `currency`
- `method`
- `formula`

권장 audit 필드:

- `source_document_id`
- `source_type`
- `form`
- `source_url`
- `filing_url`
- `input_fact_ids`
- `adjustments`
- `confidence`
- `quality_flags`
- `quality_status`
- `version`

### 19.3 Drawer 구조

1. fact name/value/unit/period
2. actual/estimate/manual/derived basis
3. source and document identity
4. available_at and point-in-time meaning
5. method and formula
6. input fact links
7. adjustments
8. quality/confidence/flags
9. source URL — 실제 값이 있을 때만
10. raw evidence disclosure — 안전한 public subset만

drawer는 focus trap, Escape 닫기, opener focus 복원, keyboard scroll, accessible
title을 지원한다. JSON dump를 기본 화면으로 노출하지 마라.

## 20. System — 공급자와 운영 상태

실제 endpoint:

- `GET /api/v1/system/providers`

Provider row는 다음을 제공한다.

- provider_id
- label
- capabilities
- contract_available
- configured
- verification: configuration_only, contract_only, not_available
- required_env — 이름만 표시 가능, 값은 절대 표시 금지
- state

System 화면은 다음을 구분해야 한다.

- contract 존재 여부
- credential/configuration 존재 여부
- 단순 설정됨과 실제 reachability 확인
- source row 존재와 최신성
- last sync/backup — 계약에 있을 때만

`configured`를 `live` 또는 `healthy`로 번역하지 마라. `contract_only`를 실제
수집 완료로 표시하지 마라. env var 값, 토큰 일부, connection string을 UI나
로그에 노출하지 마라.

## 21. 실제 프론트엔드 데이터 상태 계약

공유 envelope:

```ts
type ContractEnvelope<T> = {
  data: T | null;
  state: {
    status:
      | "ready"
      | "partial"
      | "stale"
      | "configured"
      | "fixture_non_production"
      | "missing_source"
      | "missing_contract"
      | "missing_key"
      | "rate_limited"
      | "upstream_error";
    available: boolean;
    data_mode:
      | "source_backed"
      | "configuration_only"
      | "fixture_non_production"
      | "unavailable";
    reason: string | null;
  };
  meta: Record<string, unknown>;
};
```

status와 data_mode 조합은 고정이다.

| status | available | data_mode | UI 원칙 |
| --- | ---: | --- | --- |
| ready | true | source_backed | 값 + 기준일 + audit |
| partial | true | source_backed | 유효 subset + 누락 reason |
| stale | true | source_backed | 값 유지 + stale timestamp |
| configured | true | configuration_only | System에서만 설정 상태로 표현 |
| fixture_non_production | true | fixture_non_production | 지속적 비운영 라벨 |
| missing_source | false | unavailable | `data=null`, 수집 action |
| missing_contract | false | unavailable | `data=null`, 계약 필요 |
| missing_key | false | unavailable | `data=null`, env name/action |
| rate_limited | false | unavailable | `data=null`, retry guidance |
| upstream_error | false | unavailable | `data=null`, safe retry/error |

다음 조합을 받아들이지 마라.

- unavailable status인데 `data`가 존재
- available status인데 `data=null`
- ready인데 fixture mode
- configured인데 source-backed financial number
- reason이 필요한 상태인데 reason 없음

프론트에서 runtime validation을 유지하고 위반하면 값을 숨긴 뒤 계약 오류를
표시한다.

## 22. 실제 API 의존성 지도

아래 endpoint를 읽고 기존 fetch와 normalization을 보존하라. 디자인 때문에
provider를 browser에서 직접 호출하지 마라.

### Company core

- `GET /api/v1/companies/{id}/snapshot`
- `GET /api/v1/companies/{id}/valuation-map`
- `GET /api/v1/companies/{id}/financials`
- `GET /api/v1/companies/{id}/performance`
- `GET /api/v1/companies/{id}/forecast-snapshots`
- `GET /api/v1/companies/{id}/analyst-scorecard`
- `GET /api/v1/companies/{id}/consensus`
- `GET /api/v1/companies/{id}/peers?kind=business|valuation`

### Extended company research

- `GET /api/v1/companies/{id}/fun-graphs`
- `GET /api/v1/companies/{id}/fiscal-fitness`
- `GET /api/v1/companies/{id}/health-check`
- `GET /api/v1/companies/{id}/use-of-cash`
- `GET /api/v1/companies/{id}/research-report`
- `GET /api/v1/companies/{id}/research-metadata`
- `GET /api/v1/companies/{id}/data-audit`

### Discovery and ownership

- `GET /api/v1/securities/search`
- `GET /api/v1/screener`
- `GET /api/v1/watchlist`
- `POST /api/v1/watchlist/items`
- `DELETE /api/v1/watchlist/items/{ticker}`
- `GET /api/v1/portfolio`
- `POST /api/v1/portfolio/import`

### System and context

- `GET /api/v1/system/readiness`
- `GET /api/v1/system/source-coverage`
- `GET /api/v1/system/priority-universe`
- `GET /api/v1/system/kr-valuation-cache-coverage`
- `GET /api/v1/system/providers`
- `GET /api/v1/macro-series`
- `GET /api/v1/industry-series`

### Chart persistence/export

- `GET /api/v1/chart-layouts`
- `POST /api/v1/chart-layouts`
- `DELETE /api/v1/chart-layouts/{layout_id}`
- valuation-map SVG/PNG and chart-run endpoints — 실제 코드에서 경로 확인

현재 `page.tsx`는 많은 endpoint를 병렬 로드한다. 디자인 refactor 시 다음을
보존하거나 개선하라.

- auth 401/403 처리
- timeout
- request race protection
- KR priority source gate
- source-backed cache 검증
- legacy payload normalization
- chart layout touched state
- watchlist touched state

디자인을 단순화한다는 이유로 안전 gate를 삭제하지 마라.

## 23. 컴포넌트 아키텍처

### 23.1 먼저 재사용할 기존 컴포넌트

- `BrandMark`
- `SearchOverlay`
- `HistoricalControlsPanel`
- `HistoricalMapPanel`
- `EvidenceRail`
- `GraphKeyLedger`
- `ForecastLab`
- `FinancialsPanel`
- `PerformancePanel`
- `AnalystScorecardPanel`
- `FunGraphsPanel`
- `FiscalFitnessPanel`
- `HealthCheckPanel`
- `UseOfCashPanel`
- `ScreenerPanel`
- `PortfolioPanel`
- `DataAuditPanel`
- `ResearchReportPanel`
- `ConsensusPanel`
- `PeersPanel`
- `ProviderStatusPanel`
- `KrSourceReadinessCard`

기존 component의 상태·동작·테스트를 읽지 않고 같은 기능을 새로 만들지 마라.

### 23.2 권장 분리 대상

현재 `apps/web/app/page.tsx`는 shell, fetch orchestration, URL state, 여러 inline
panel을 함께 가진다. 한 번에 재작성하지 말고 다음 순서로 작은 추출을 고려하라.

- `TerminalShell`
- `GlobalRail`
- `TerminalTopbar`
- `CompanyHeader`
- `CompanyWorkspaceTabs`
- `DataStateBanner`
- `FactAuditTrigger`
- `TraceBadge`
- `DenseMetricGrid`
- `DenseDataTable`
- `ResponsiveEvidenceDrawer`
- `WorkspaceErrorBoundary`

새 component는 반복을 실제로 줄일 때만 만든다. component 이름만 늘리고 prop
drilling을 악화시키지 마라. state management library를 새로 추가하기 전에 현재
React state와 URL state로 해결 가능한지 확인하라.

### 23.3 Server/Client 경계

- 현재 app이 client-heavy인 이유를 먼저 확인한다.
- browser interaction, selected chart year, local controls만 client state에 둔다.
- redirect-only route를 client-side 우회로 덮지 말고 의미 있는 server shell과
  공유 workspace component 경계를 만든다.
- server/client hydration에서 ticker/tab state가 흔들리지 않게 한다.
- 새로운 data library나 chart library는 실질적 이득과 bundle 영향이 검증될 때만
  추가한다.

## 24. 상호작용 계약

### 24.1 검색

- ticker, company name, workspace를 검색한다.
- keyboard focus와 arrow navigation을 지원한다.
- 결과에 market과 ticker를 함께 표시한다.
- unsupported ticker를 선택하거나 URL로 입력해도 숫자를 노출하지 않는다.
- 최근 검색과 quick ticker는 source mode를 명확히 한다.

### 24.2 탭과 URL

- tab click은 즉시 active state와 URL을 갱신한다.
- browser back/forward가 동작한다.
- 새로고침이 ticker와 workspace를 보존한다.
- route alias가 같은 terminal state로 수렴한다.
- active tab은 `aria-current` 또는 적절한 tab semantics로 표시한다.

### 24.3 차트

- hover, click, keyboard selection을 지원한다.
- selected year를 모든 관련 panel과 동기화한다.
- period preset과 custom date가 서로 모순되지 않게 한다.
- chart settings를 닫아도 현재 선택을 잃지 않는다.
- series toggle은 aria-pressed와 시각 상태를 함께 제공한다.
- saved layout은 저장/적용/삭제 결과를 알려준다.
- export는 현재 source state와 선택을 보존한다.

### 24.4 Audit

- 숫자 click 또는 Enter/Space로 drawer를 연다.
- drawer를 닫으면 focus가 원래 숫자로 돌아간다.
- source document link는 새 탭임을 알려준다.
- unsafe raw payload를 그대로 렌더링하지 않는다.

### 24.5 비동기 상태

- loading 중 기존 선택을 유지한다.
- 느린 이전 요청이 새 요청을 덮어쓰지 않는다.
- retry는 실제 fetch를 다시 실행한다.
- mutation 실패는 성공처럼 보이지 않는다.
- toast만으로 중요한 오류를 전달하지 말고 해당 control 근처에 상태를 둔다.

## 25. 키보드와 단축키

다음은 기존 충돌이 없을 때 구현하거나 보존할 목표다.

- `/`: search focus
- `Escape`: overlay/drawer/modal 닫기
- Left/Right: tablist 또는 segmented control 이동
- Up/Down: search result와 chart year 이동
- Enter/Space: 선택/토글/audit 열기
- `?`: shortcut help — 구현할 경우에만 표시

단축키는 input, textarea, select 편집 중 실행되지 않아야 한다. 단축키가 없어도
모든 기능을 접근할 수 있어야 한다.

## 26. 반응형 계약

### 26.1 ≥ 1440px desktop

- chart가 above the fold의 지배적 영역
- facts rail 동시 노출
- primary controls 한 줄 또는 안정적인 두 줄
- table과 chart가 page horizontal scroll을 만들지 않음
- company header와 tabs가 과도한 높이를 차지하지 않음

### 26.2 1024–1439px laptop

- rail 축소 또는 유지, plot 우선
- controls가 겹치지 않고 논리적으로 wrap
- facts rail은 좁아지거나 collapsible 가능
- primary tabs 모두 keyboard reachable
- chart 최소 가독 폭 유지

### 26.3 768–1023px tablet

- global rail은 compact nav로 전환 가능
- chart와 evidence를 stack 또는 drawer로 전환
- data table는 내부 horizontal scroll
- source status는 사라지지 않음

### 26.4 ≤ 767px mobile

mobile은 desktop terminal 전체를 축소 복제하지 않는다. 다음 핵심만 완결한다.

- Snapshot
- Watchlist/More entry
- simplified Historical chart
- Forecast 핵심 시나리오
- Fact Audit drawer
- mobile evidence summary
- bottom navigation

현재 `MobileBottomTabs`와 `MobileEvidenceSummary`를 보존·개선한다. fixed bottom UI가
content와 drawer button을 가리지 않게 safe-area padding을 적용한다.

### 26.5 responsive 실패 조건

- page 전체 horizontal overflow
- control overlap
- clipped text 때문에 상태 의미가 사라짐
- chart가 0 높이 또는 극단적으로 작아짐
- fixed nav가 audit action을 가림
- tooltip만 존재하고 mobile에서 접근 불가

## 27. 접근성 계약

WCAG 2.2 AA를 목표로 한다.

- semantic landmark: nav, main, header, section, aside
- heading hierarchy를 건너뛰지 않는다.
- 모든 input은 visible label 또는 정확한 accessible name을 갖는다.
- icon-only button은 `aria-label`이 필요하다.
- tab/segmented control은 올바른 role/aria state를 갖는다.
- 현재 선택은 색상 외 indicator를 갖는다.
- focus ring을 제거하지 않는다.
- keyboard trap은 modal/drawer 내부의 의도된 focus trap만 허용한다.
- overlay 닫기와 focus restoration을 구현한다.
- text contrast와 non-text contrast를 확인한다.
- chart 정보는 표/legend/readout으로도 제공한다.
- positive/negative, actual/estimate, ready/error를 색상만으로 구분하지 않는다.
- reduced motion을 존중한다.
- loading 변화가 screen reader에 과도하게 반복되지 않게 한다.
- touch target은 mobile에서 충분한 크기를 갖는다.

## 28. 콘텐츠와 숫자 표기

### 28.1 제품 언어

현재 terminal chrome의 영문 label을 기본으로 유지한다. 번역을 부분적으로
섞어 화면을 불안정하게 만들지 마라. 한국 공시명이나 회사 설명은 원문일 수
있지만, navigation과 상태 용어는 일관되어야 한다. 향후 localization이 필요하면
copy dictionary로 분리할 수 있게 작성한다.

### 28.2 숫자

- currency, unit, period를 함께 표시한다.
- KRW와 USD를 같은 숫자처럼 비교하지 않는다.
- percent와 percentage point를 구분한다.
- 음수의 sign을 보존한다.
- 0은 실제 0일 때만 표시한다.
- missing은 `—`, `Not available`, 또는 구체적 reason으로 표시한다.
- 반올림 정책을 화면마다 바꾸지 않는다.
- full precision은 audit에서 확인 가능하게 한다.
- estimate에는 `E`, forecast, 또는 명시적 label을 붙인다.

### 28.3 상태 카피 원칙

짧은 결론 + 이유 + 다음 행동 구조를 사용한다.

예:

- `Source required` — OpenDART/pykrx/marcap source rows are not loaded.
- `Contract unavailable` — No licensed consensus contract is configured.
- `Credential missing` — Add the named environment variable locally; never
  display its value.
- `Partial coverage` — Valid rows are shown; missing years are listed below.
- `Stale data` — Last source-backed value is shown as of {timestamp}.
- `Rate limited` — No substitute data was displayed. Retry after the provider
  window resets.

카피가 투자 권유처럼 들리지 않게 한다. Buy, Sell, Strong Buy, guaranteed,
undervalued 같은 결론을 자동 생성하지 마라.

## 29. 인증·보안·공개 저장소 규칙

- owner session loading, sign-in, authenticated 상태를 보존한다.
- 401/403에서 금융 화면을 잠깐 노출하지 않는다.
- GitHub allowlist나 인증 구현을 디자인 때문에 우회하지 않는다.
- 모든 tracked file과 committed screenshot은 공개될 수 있다고 가정한다.
- env var는 이름만 다루고 값은 로그·DOM·문서에 쓰지 않는다.
- 개인 portfolio CSV와 raw source payload를 fixture로 커밋하지 않는다.
- third-party source URL을 렌더링할 때 안전한 protocol을 확인한다.
- raw JSON rendering은 secret/PII filtering이 확인된 public subset만 사용한다.
- 오류 메시지에 local filesystem path, token, connection string을 넣지 않는다.

## 30. 성능과 안정성

- route 전환마다 모든 chart asset을 불필요하게 재생성하지 않는다.
- 큰 SVG/plot에서 memoization 필요성을 실제 profiler 또는 render 경계로 판단한다.
- control input은 적절히 debounce한다.
- fetch cancellation과 stale response protection을 유지한다.
- table row가 많으면 pagination/virtualization을 검토하되 먼저 실제 규모를
  확인한다.
- layout shift를 줄인다.
- search overlay와 drawer가 전체 page rerender를 유발하지 않게 한다.
- 새 dependency는 bundle, 유지보수, 보안 이득이 명확할 때만 추가한다.
- Next.js 현재 버전 문서를 `node_modules/next/dist/docs/`에서 확인하고 과거 지식으로
  API를 추정하지 않는다.

## 31. 구현 순서

다음 순서를 지켜라. 앞 단계 검증 없이 뒤 기능을 넓히지 마라.

### Phase 0 — 안전한 기준선

1. AGENTS/DECISIONS/이 문서를 읽는다.
2. git status와 현재 변경 범위를 기록한다.
3. 기존 앱을 실행한다.
4. 현재 핵심 route를 캡처한다.
5. 기존 typecheck, lint, relevant UI test를 실행해 baseline을 기록한다.
6. baseline failure와 새 failure를 구분한다.

### Phase 1 — 레퍼런스 분석

1. 공개 Historical/Forecasting reference를 캡처한다.
2. current LUXON과 동일 viewport 비교 보드를 만든다.
3. route별 user goal, data dependency, interactions, states, audit targets,
   keyboard, acceptance를 표로 정리한다.
4. P0/P1/P2 gap을 분류한다.

### Phase 2 — Design foundation

1. 기존 token을 inventory한다.
2. 반복 magic value만 token으로 승격한다.
3. terminal shell, topbar, company header, tabs의 높이와 위계를 안정화한다.
4. DataStateBanner/TraceBadge/FactAuditTrigger의 공통 패턴을 정한다.
5. light desktop을 우선 완성하고 기존 dark token을 깨지 않는다.

### Phase 3 — Historical P0

1. chart 위의 불필요한 hero height를 제거/이동한다.
2. compact header/tabs/control band를 만든다.
3. chart와 facts rail을 above the fold에 배치한다.
4. line semantics, legend, selected year, audit targets를 맞춘다.
5. ready/partial/stale/missing/fixture 상태를 구현한다.
6. 1440, 1280, 1024에서 비교하고 수정한다.

### Phase 4 — FnGuide형 core

1. Snapshot dense hierarchy
2. Financials table usability
3. Consensus contract states
4. Peers business/valuation contract states
5. 모든 숫자의 audit link

### Phase 5 — Forecast/Performance

1. evidence lane 분리
2. controls와 request state
3. deterministic result presentation
4. performance selection 동기화
5. analyst scorecard coverage state

### Phase 6 — Global workspaces

1. Screener
2. Watchlist
3. Portfolio
4. Data Audit
5. System/providers

### Phase 7 — Responsive/accessibility

1. 1024 layout
2. tablet
3. 390×844 mobile
4. keyboard journey
5. focus/focus restoration
6. chart non-color alternative

### Phase 8 — Final QA

1. source와 implementation을 같은 비교 보드에서 재검토한다.
2. 모든 P0/P1 defect를 수정한다.
3. route, state, interaction tests를 실행한다.
4. `design-qa.md`를 완성한다.
5. working preview를 열어 둔다.
6. 확인하지 못한 live/API/deployment gate를 명시한다.

## 32. 테스트 계약

실제 `package.json` scripts를 먼저 확인하고 저장소 표준 명령을 사용한다. 최소한
다음을 검증한다.

- lint
- TypeScript typecheck
- production build
- relevant Playwright UI tests
- 기존 API contract tests가 frontend-only 변경으로 깨지지 않았는지 필요 범위
- responsive screenshots
- keyboard-only core journey
- focus restoration
- unavailable state rendering
- unsupported ticker fail-closed
- no fixture promotion for KR priority ticker

기존 테스트에서 반드시 지켜야 할 핵심 동작:

- URL ticker/tab state
- search and tab switching
- Historical controls
- Forecast mode/case/year
- Consensus fail-closed state
- Peers toggle
- Provider System state
- Data Audit navigation
- mobile evidence and navigation

테스트를 통과시키기 위해 안전 gate나 assertion을 약화하지 마라. UI copy를
바꾸었다면 brittle text selector를 더 견고한 role, label, test id로 개선하되,
사용자-visible semantics를 보존한다.

## 33. Visual QA 계약

루트 `design-qa.md`에 다음 구조로 기록한다.

```md
# LUXON Design QA

## Scope and routes
## Reference evidence
## Viewports and states
## Typography
## Spacing and alignment
## Color and chart semantics
## Borders, radii, elevation
## Assets and brand/IP boundary
## Interactions
## Data states and source_trace
## Accessibility
## Responsive behavior
## Automated verification
## Known external gates
## Residual P2 items

final result: passed
```

실제 P0/P1 결함이 남았거나 reference capture가 불가능하면 `passed`라고 쓰지
마라. 다음처럼 끝내라.

```md
final result: blocked
blocker: <정확한 재현 조건과 필요한 입력>
```

P0/P1/P2 기준:

- P0: 숫자 안전성, source gate, route/auth failure, 핵심 task 불가, severe a11y
- P1: chart hierarchy, control overlap, facts rail, important state/interaction 누락
- P2: 미세 spacing, secondary animation, 장식적 polish

현재 구현의 P0는 §6.3의 8개 항목이다. 이 8개를 모두 닫은 뒤 companion QA의
나머지 blocking product decisions와 route/responsive/accessibility acceptance를
P1 DoD로 검증한다. 장식적 polish만 P2로 남길 수 있다.

`docs/CLAUDE_DESIGN_QA_CHECKLIST.md`의 체크박스와 상태 매트릭스는 이 섹션보다
더 구체적인 필수 계약이다. P0 또는 P1 항목을 임의로 제외하거나 "후속 작업"으로
미루고 `passed`를 선언하지 마라.

## 34. Route별 수용 기준

### `/terminal` — Search / resume

- 검색이 첫 행동이며 ticker와 회사명으로 탐색 가능하다.
- 최근 회사와 의미 있는 route/query state를 복원한다.
- source/provider health는 compact warning이며 전체 ops 화면이 아니다.
- unsupported ticker는 이전 회사 숫자 없이 fail closed한다.

### `/company/[id]/graph` — Historical Graph

- 1440px에서 chart와 facts rail이 above the fold에 존재한다.
- company header/tabs/controls가 plot을 과도하게 아래로 밀지 않는다.
- metric, period, settings 조작이 겹치지 않는다.
- actual/forecast와 각 valuation line을 구분한다.
- selected point가 rail과 audit에 동기화된다.
- source 없는 KR ticker에 숫자가 없다.

### `/company/[id]/snapshot`

- dense company overview가 한 화면에서 스캔된다.
- metric마다 단위/기간/source access가 있다.
- valuation preview가 Historical로 연결된다.
- missing value가 0으로 보이지 않는다.

### `/company/[id]/financials`

- statement/period/basis controls의 가능·불가능 상태가 정확하다.
- actual/estimate가 구분된다.
- table header/row label usability가 유지된다.
- 모든 visible financial cell이 audit 가능하다.

### `/company/[id]/forecast`

- external/manual/AI lane이 구분된다.
- 1Y–5Y와 case/mode 변경이 deterministic result를 갱신한다.
- missing consensus가 가짜 case를 만들지 않는다.
- result와 source/audit가 연결된다.

### `/company/[id]/consensus`

- validated cases만 표시된다.
- provider/evidence kind/quality가 보인다.
- missing_contract와 missing_source가 구분된다.
- revision history를 계약 없이 발명하지 않는다.

### `/company/[id]/peers`

- business/valuation toggle이 동작한다.
- relationship과 source가 보인다.
- facts가 없으면 비교 숫자가 없다.
- peer를 sector label만으로 추론하지 않는다.

### `/screener`

- filter, active filter, results, no-result, unavailable이 구분된다.
- 결과 행이 company deep link로 연결된다.
- 단위와 coverage가 보인다.

### `/portfolio`

- import validation과 저장 성공/실패가 구분된다.
- 실제 imported records만 chart marker가 된다.
- personal data를 fixture/commit에 넣지 않는다.

### `/system`

- contract/configuration/reachability/source rows가 구분된다.
- env var 값이 보이지 않는다.
- missing_contract/missing_key/configured가 정확히 표현된다.

### mobile

- Snapshot, simplified chart, Forecast, Audit이 완결된다.
- bottom nav가 content를 가리지 않는다.
- chart 핵심 정보가 text/readout으로도 제공된다.
- horizontal page overflow가 없다.

## 35. Definition of Done

다음을 모두 충족해야 완료다.

- 실제 코드를 수정했다.
- core route가 실행된다.
- FAST Graphs형 분석 순서와 정보 밀도가 명확하다.
- LUXON 브랜드와 IP 경계가 지켜졌다.
- Snapshot/Financials/Consensus/Peers가 FnGuide형 정보 구조로 정돈됐다.
- source trace 없는 숫자를 표시하지 않는다.
- 모든 data status가 구분된다.
- desktop/laptop/mobile 핵심 흐름이 작동한다.
- keyboard로 주요 흐름을 완료할 수 있다.
- reference와 implementation을 동일 viewport에서 비교했다.
- typecheck, lint, build, relevant tests 결과를 기록했다.
- `design-qa.md`가 존재하고 정직한 final result를 가진다.
- local preview를 사용자가 열 수 있다.
- 외부 API key, licensed data, Docker engine, deployment 같은 남은 gate를
  구현 완료와 구분했다.

## 36. 금지된 지름길

다음 행동은 하지 마라.

- 컨셉 문서만 작성하고 구현했다고 보고
- 레퍼런스를 열지 않고 기억으로 FAST Graphs를 재현
- 화면 캡처만 하고 같은 보드에서 비교하지 않음
- chart를 CSS 장식이나 정적 이미지로 대체
- 제3자 screenshot을 shipped asset으로 사용
- 새로운 금융 숫자나 peer를 fixture로 만들어 빈 화면 채움
- `null`을 0으로 변환
- data state reason 숨김
- `configured`를 `live`로 표현
- API key나 env value를 UI에 표시
- 브라우저에서 외부 provider 직접 호출
- backend contract를 프론트 편의를 위해 무단 변경
- 현재 사용자 변경을 reset/checkout으로 제거
- 대규모 rewrite 후 한 번만 테스트
- 접근성 없는 custom select/tab/drawer 제작
- mobile을 desktop 축소판으로만 처리
- 테스트 실패를 숨기고 passed 기록
- 검증하지 않은 deployment/live data를 완료로 표현

## 37. Claude의 작업 중 의사결정 규칙

갈등이 생기면 다음 우선순위로 결정하라.

1. 숫자 안전성과 source trace
2. 사용자의 핵심 분석 흐름
3. 기존 동작과 route 호환성
4. 접근성
5. FAST Graphs형 정보 위계와 밀도
6. FnGuide형 한국 기업 정보 구조
7. LUXON 브랜드 일관성
8. responsive usability
9. 성능
10. 장식적 polish

디자인 선택을 설명할 때 "더 예뻐서"가 아니라 다음 중 하나로 근거를 제시하라.

- 사용자가 핵심 값을 찾는 시간 단축
- chart plot 면적 확보
- actual/estimate/source state 오해 감소
- keyboard/screen-reader 접근 향상
- 작은 화면 overflow 해소
- 코드 중복과 상태 불일치 감소

## 38. Claude 최종 응답 형식

최종 응답은 한국어 존댓말로 쓰고, 결과부터 제시한다. 과장하지 말고 다음 순서를
지킨다.

1. **로컬 미리보기** — 실제 열 수 있는 URL과 기본 확인 route
2. **최종 결과** — 무엇이 실제로 구현되었는지 3–6문장
3. **핵심 UI/UX 변화** — Historical, FnGuide형 core, Audit, responsive
4. **변경한 핵심 파일** — 파일과 역할
5. **중요 설계 판단** — FAST Graphs형 workflow와 LUXON 독자성의 경계
6. **검증 결과** — 실행한 명령과 pass/fail 수치
7. **현재 작동 범위** — 실제 data와 fixture를 분리
8. **남은 외부 연결** — API key, licensed provider, live ingestion, Docker,
   deployment 등
9. **Design QA** — `design-qa.md` final result

보고 예시의 원칙:

- `AAPL fixture_non_production으로 시각 회귀 확인`은 가능
- `KR 실데이터 구현 완료`는 source-backed E2E를 확인했을 때만 가능
- `Docker 구성 검증`과 `Docker image 실제 실행`을 구분
- `코드와 테스트 완료`와 `배포 완료`를 구분

변경하지 않은 것을 변경했다고 말하지 마라. 테스트하지 않은 것을 통과했다고
말하지 마라. 마지막에는 사용자가 확인할 구체적 화면 하나를 지정하라.

## 39. 지금 실행할 작업

이제 다음을 수행하라.

1. 저장소와 현재 화면을 읽고 캡처한다.
2. 공개 FAST Graphs Historical/Forecasting과 FnGuide 정보 구조를 확인한다.
3. 동일 viewport 비교를 만든다.
4. Historical P0를 가장 먼저 정리한다.
5. Snapshot, Financials, Consensus, Peers를 FnGuide형 밀도로 통합한다.
6. source_trace와 모든 fail-closed 상태를 UI에 완성한다.
7. route, keyboard, responsive, test를 검증한다.
8. `docs/CLAUDE_DESIGN_QA_CHECKLIST.md`의 P0/P1을 전부 재검증한다.
9. `design-qa.md`를 작성한다.
10. local preview를 열어 둔 뒤 사실 기반으로 보고한다.

계획만 반환하지 말고 구현까지 진행하라.

# END PROMPT

---

## 유지보수 메모

이 프롬프트는 현재 코드 계약을 기준으로 한다. 다음 변경이 생기면 함께 갱신한다.

- `ContractDataStatus` 또는 `DataMode` 변경
- company route alias 변경
- 새로운 source-backed Consensus/Peers contract
- Financials period/basis contract 확장
- chart layer 또는 deterministic formula 변경
- public reference URL 변경
- Claude가 수정할 수 있는 ownership boundary 변경

이 문서가 코드와 충돌하면 `AGENTS.md`, 실제 API schema, 생성된 타입, 테스트를
우선하고 충돌을 문서에 기록한다. 코드에 없는 숫자나 기능을 문서가 요구한다는
이유로 발명하지 않는다.
