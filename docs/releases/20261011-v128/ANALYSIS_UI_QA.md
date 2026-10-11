# 실적 기간·비교 가능 구간 UI 검증

2026-10-11. 로컬 v128 후보 코드와 `fixture_non_production` 자료를 검증한 기록입니다. 운영 자료 확보율이나 공개 배포 완료의 증거가 아닙니다.

## 바뀐 동작

- `기업 분석 → 실적과 주가 → 실적의 흐름`에서 최근 3·5·10년은 마지막 확인된 회계연도까지 연속된 연도입니다. 원자료가 2021·2023·2024년에만 있으면 최근 3년은 2022·2023·2024이며 2022년 값은 `null`과 ‘자료 없음’으로 표시합니다.
- 현재 재무 응답에는 결산월·조회 기준일 계약이 없습니다. 브라우저의 현재 연도로 미수집 기간을 추정하지 않고 `2024 회계연도까지`처럼 실제 자료의 끝을 표시합니다. 비정형 기간명만 있으면 ‘3개 기간’으로 표시합니다. `전체`에서는 보존된 원래 기간을 모두 열 수 있습니다.
- 실제 0은 0, 적자는 음수로 표시합니다. 결측은 막대·증감률·이익률의 0으로 바꾸지 않습니다. 분기 메뉴도 확인된 분기 관측값만 표시합니다.
- 실적과 주가에는 서버의 `comparison_eligible`을 통과하고 실제 비교값이 있는 시작·종료 날짜를 표시합니다. 사이에 비교할 수 없는 날짜가 있으면 ‘중간 공백 있음’을 붙입니다. 단일 날짜와 비교 불가 상태를 구분합니다.
- 비교선 산식·서버의 차단 기준·조회 기간·선택 날짜·확대 기준·CSV 생성 경로는 유지합니다.

## 사용자·데이터 계약

| 항목 | 확인 |
|---|---|
| 목적 | 최근 실적의 누락 연도를 숨기지 않고, 주가 전체 이력과 비교선의 확보 기간을 구분 |
| 데이터 | 기존 CFS 실제 실적, 원문 출처가 연결된 재무 응답, 검증 여부를 포함한 valuation 응답 |
| 조작 | 기존 연간/분기·3/5/10년/전체·지표·배수·날짜·표·CSV 버튼 재사용 |
| 설명·출처 | 기존 ‘뜻 보기’, 공시 EPS·주식 수 확인 동선 유지. 새 출처나 검증 승격 없음 |
| 키보드 | 기존 native button/input/select 역할과 초점 동작 유지. 누락 표 셀에 연도·지표·자료 없음 접근성 이름 추가 |
| 로딩·오류 | 기존 조회·오류·재시도 상태 유지. 가용 범위 문구는 현재 선택의 비교값이 있을 때만 표시 |
| 참고 | 기존 FnGuide 공개 캡처와 현재 분석 화면 감사 기록, FAST Graphs 지표·공개시점 설계 계약. 유료 화면을 새로 검사한 것으로 표현하지 않음 |

## 검증

`apps/web`에서 설치된 Node/도구 바이너리를 사용했습니다. pnpm은 자동 의존성 설치를 시도한 뒤 비대화형 모듈 제거 보호로 중단되어, 의존성 변경 없이 직접 실행했습니다.

```powershell
node --test tests/market/analysis-periods.node.test.mjs tests/market/financial-chart.node.test.mjs
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js components/market/analysis-periods.ts components/market/earnings-overview.tsx components/market/valuation-chart.tsx tests/market/analysis-periods.node.test.mjs
$env:MARKET_WEB_URL='http://127.0.0.1:3260'
node node_modules/@playwright/test/cli.js test -c playwright.market.config.ts tests/market/analysis-periods.spec.ts --reporter=list --output=../../work/v128-analysis-browser
```

- 순수 함수·실제 React 렌더·차트 상태 회귀 **19개 통과**. 누락 연도, 비12월 표기, 비정형 기간, 실제 0·음수, 추정치/별도 제외, 비교 불가점, 날짜·확대 기준 유지, CSV 값 일치를 확인했습니다.
- Playwright **3개 통과**: 390·768·1440px에서 기간 전환·누락 셀·가용 범위·배수 변경 후 날짜 보존·페이지 가로 넘침을 확인했습니다. 모든 API와 WebSocket은 테스트 안에서 대체했으며 실제 공급자를 호출하지 않았습니다.
- 1440px 화면 캡처를 위해 해당 테스트 1개를 추가 실행해 통과했습니다. 캡처는 `work/v128-analysis-visual/analysis-periods-fiscal-ga-2baac-nge-remain-usable-at-1440px/fixture-earnings.png`, `fixture-comparison.png`입니다. 실제 재무 수치가 아닙니다.
- 변경 TSX/helper/순수 테스트의 ESLint 통과. 최초 전체 TypeScript 검사는 동시 개편 중인 `detail.tsx`·`dashboard.tsx`의 오류로 종료됐으며, 이번 소유 파일 오류는 없었습니다. 통합 수정 뒤 전체 검사는 릴리스 담당자가 확인합니다.

## 남은 판정

공개 주소의 동일 버전 반영, 운영 응답을 사용한 화면 확인, 전체 실사용·확대·다크모드 검증은 통합 배포 단계의 별도 조건입니다. 자동 스크롤 후 요소 캡처에는 고정 글로벌 헤더가 조작 영역 일부에 겹쳐 찍혔으므로, 통합 담당자가 실제 스크롤 위치별 가림을 확인해야 합니다. 캡처만으로 초기 화면의 가림 오류라고 단정하지 않습니다.
