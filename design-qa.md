# 주식담 — 화면 개편 검증

검증일: 2026-10-09 KST. 아래 보고서는 현재 `/stocks` 화면에 적용됩니다. 문서 하단의 2026-08-16 LUXON 기록은 삭제하지 않고 과거 자료로 보존했습니다.

**final result: passed** — 선택한 주식담의 시장 배치, 모바일 조작, 5분 선 차트, 청색 실적 비교선과 다크모드 심볼을 수정 후 캡처에서 확인했습니다. 마지막 비교선·로고는 공개 v89를 인앱 브라우저에서 다시 확인했습니다. 이 판정은 아래에 명시한 화면 개편 범위이며 전 종목 수집·실시간·성능·실사용 검증의 완료를 뜻하지 않습니다.

## 기준 이미지와 검증 환경

- 사용자가 선택한 **표시 순서 3번, 주식담** 이미지를 기준으로 사용했습니다: `C:/Users/lch68/.codex/generated_images/01a0d161-b6d9-7250-8848-4ddf0aaa0a48/exec-79227a7b-8245-4d91-b86b-4962fcce7040.png`.
- 원본은 1657×949px의 PC·휴대폰 합성 시안입니다. 실제 CSS 뷰포트와 배율 정보가 없는 시안이므로 픽셀 일치로 평가하지 않았습니다. PC와 휴대폰의 앱 내부를 각각 같은 시장·라이트·기본 글씨 상태와 비교했습니다. 기기 프레임, 생성된 예시 기사·수치와 개발 도구 표시는 제품의 재현 대상에서 제외했습니다.
- 로컬 실행: `http://127.0.0.1:3248/stocks`. 디자인 증거는 사용자가 승인한 **인앱 브라우저**에서 부모 에이전트가 직접 조작·캡처했습니다. Playwright는 별도로 상태·오류·회귀 검증에 사용했습니다.
- PC 요청 뷰포트 1440×900, 실제 캡처 1425×891px. 모바일 요청 390×844, 실제 캡처 375×812px. 앱 패널이 차지한 영역의 차이이며 2배 이미지로 간주하지 않았습니다. 원본과 구현 이미지를 같은 도구 응답에 함께 열고 전체 구조와 헤더·순위·종목 행을 비교했습니다. 별도 이미지 편집이나 임의 배율 보정은 하지 않았습니다.
- 기준 글꼴은 기존 자체 제공 Pretendard 및 시스템 대체 글꼴입니다. 기존 Lucide 아이콘과 검증된 기업 로고를 재사용했습니다. 주식담 심볼은 별도로 생성한 투명 PNG이며 CSS 그림이나 글자 기호로 대체하지 않았습니다.

## 전체 화면과 구역별 증거

경로는 저장소 루트를 기준으로 합니다.

| 캡처 | 상태와 확인 범위 |
|---|---|
| `work/operations-20261008/jusikdam-pc-v2.png` | 시장 라이트, 순위 8행이 첫 화면에 보임, 넓은 표와 관련 소식·관심·최근 영역 |
| `work/operations-20261008/jusikdam-mobile-v3.png` | 시장 라이트, 로고·설정 1행과 별도 검색, 지수 두 행, 순위 선택·전체 시장 문구 잘림 없음 |
| `work/operations-20261008/jusikdam-mobile-dark-large.png` | 다크·가장 큰 글씨, 이름·가격 줄바꿈과 행 높이 증가, 페이지 가로 넘침 없음. 심볼 밝기 수정 전 자료 |
| `work/operations-20261008/jusikdam-price-1440.png` | 실제 PC 시세, 짧은 종목 요약·차트·시점 이동·데이터 표, 제목이 차트를 가리지 않음 |
| `work/operations-20261008/jusikdam-price-mobile-5min.png` | 모바일 실제 5분 구간 종가, 이전 1분 밀집 선의 수직 띠 현상 완화, 원본 1분 선택 제공 |
| `work/operations-20261008/jusikdam-detail-pc-v2.png` | 실제 PC 기업 분석, 목차·지표·기간·배수·차트·시점 수치. 청색 비교선 수정 전 자료 |
| `work/operations-20261008/iab-public-v89-analysis-pc.png` | 최종 공개 v89 인앱 PC 1425×891px. 정상 주식담 PNG, 초록 주가·청색 EBITDA 비교선·같은 색 범례 |
| `work/operations-20261008/iab-public-v89-analysis-mobile-dark.png` | 최종 공개 v89 인앱 모바일 375×812px. 다크 심볼·설정·검색·가격·탭과 분석 조작 잘림 없음. 차트는 이 화면 아래에 있어 차트 자체의 다크 검증으로 확대 해석하지 않음 |

주의: `jusikdam-detail-pc.png`는 파일명과 달리 375×812px의 모바일 캡처입니다. PC 증거로 사용하지 않았습니다.

## 수정 이력과 발견 사항

| 심각도·발견 | 영향과 수정 | 수정 후 증거 |
|---|---|---|
| P1 · PC 첫 화면의 순위가 6행만 보임 | 지수·제목 간격 축소, 비교 선택을 순위 도구 행으로 통합, 표의 불필요한 좌우 패딩 제거. 체크박스가 없는 표의 종목 열 왼쪽 오프셋도 제거 | `jusikdam-pc.png` → `jusikdam-pc-v2.png`, 8행 노출 |
| P1 · 모바일 헤더가 3행, 첫 종목이 화면 밖 | 보조 열 접기 버튼 숨김, 글자 크기 아이콘·설정 48px 한 행, 검색 52px 별도 행. 지수 두 행을 각 80px로 축소, 홈 편집을 더보기로 이동 | `jusikdam-mobile.png` → `jusikdam-mobile-v3.png`, 첫 종목과 다음 종목 일부 노출 |
| P2 · 모바일 시장 필터가 ‘전체 시…’로 잘림 | 퀵 순위 행을 독립 행으로 고정하고 시장 선택 최소 폭 확보 | `jusikdam-mobile-v2.png` → `jusikdam-mobile-v3.png` |
| P2 · 빈 관심목록에 같은 추가 행동 중복 | 빈 상태에서는 관심종목 추가 하나만 제공하고 헤더 추가·전체 보기 반복 제거 | PC·모바일 최종 시장 캡처 |
| P1 · 모바일 720개 분 가격을 좁은 선 차트에 표시해 수직 띠처럼 보임 | 중복 시각 0건·역전 0건을 실제 응답에서 확인. 모바일 최초 5분 구간의 실제 마지막 관측값, PC 최초 1분 원본으로 표시. 1분/5분 선택과 링크·저장 복원. 평균·보간·가짜 OHLC 생성 없음 | `chart-line-audit.json`, `jusikdam-price-mobile-5min.png`, 노드·브라우저 회귀 통과 |
| P2 · 실제 주가와 실적 비교선이 모두 초록 | 실제 주가는 초록, 실적 비교선은 라이트 청색·다크 밝은 청색, 과거 배수는 점선 호박색. 범례도 동일 토큰 사용 | `iab-public-v89-analysis-pc.png`에서 청색선·초록선 구분 확인 |
| P2 · 다크 배경에서 심볼이 어두움 | 원본 에셋 보존, 다크모드에만 밝기·채도 조정 | `iab-public-v89-analysis-mobile-dark.png`에서 심볼 형태와 이름 식별 확인. 실제 브라우저 밝기 필터·PNG 로드 검사도 통과 |
| P1 · 공개 v89의 주식담 심볼 404 | 빌드에 들어간 자산이 웹 서버 경로에서 차단됨. 부모 에이전트가 해당 서비스의 `/brand/jusikdam-symbol.png`·`/icon.png` 경로만 연결 | PNG HTTP 200·297,461바이트·브라우저 자연 너비 1,254px. 공개 캡처에서 정상 로고 확인 |

## 다섯 가지 화면 기준

| 기준 | 판정과 근거 |
|---|---|
| 글꼴·글자 위계 | 브랜드·가격·종목명과 보조 숫자의 굵기·크기 구분. 모바일 본문 18px와 큰 글씨 설정 보존. 긴 종목명은 줄바꿈하며 행 높이가 늘어남. 시안의 작은 모바일 10행을 그대로 복제하지 않고 사용자가 정한 터치·큰 글씨 기준을 우선 |
| 간격·배치 | 흰 바탕의 넓은 표와 오른쪽 소식 영역, 작은 회사 요약과 큰 차트, 기업 분석 목차 유지. PC 첫 화면 8행. 모바일은 검색→지수→순위→종목→하단 메뉴 순서, 핵심 조작 잘림 없음 |
| 색상·상태 | 라이트 짙은 초록 선택·중립 표면, 상승 적색·하락 청색, 다크·시스템 모드 보존. 그라데이션과 버튼 위치 이동 없음. 공개 인앱 최종 캡처에서 청색 비교선과 다크 심볼 렌더링 확인 |
| 이미지·아이콘 | 주식담 심볼 투명 PNG와 HTML 워드마크, 실제 회사 로고 사용. 기존 로고 누락 대체 동작 보존. 브랜드 심볼을 CSS·SVG 도형으로 그리지 않음. 아이콘은 기존 라이브러리의 같은 굵기 계열 |
| 문구·내용 | 장식 안내 대신 짧은 제목과 실제 상태 사용. 미수집 날짜는 현재 데이터로 대체하지 않음. 관련 기사 동시 등장과 검증된 사업 관계를 다른 라벨로 표시. 지표의 배수 입력 필요 상태와 실제 자료 부족 상태 구분 |

## 상태·핵심 동작 검증

- 검색·기존 관심종목·큰 글씨·다크·공유 링크의 저장 형식을 보존했습니다. 로고·문서 제목·PWA manifest·오프라인 화면은 주식담으로 맞추고 이전 아이콘 파일은 `apps/web/legacy/brand/`에 보존했습니다.
- 업종 / 산업 / 테마 / 지도는 역할을 구분합니다. 산업은 토스 산업 분류이며 검색·기간·순위·상세 구성종목 선택을 제공합니다. 키보드 방향키·Home·End와 종목 상세→복귀 상태를 검증했습니다.
- 산업 순위는 다시 보기 전 자동 재정렬하지 않습니다. 설명·구성종목 중 하나가 실패해도 나머지를 유지하고 해당 구역만 다시 요청합니다. null 숫자를 0으로 바꾸지 않습니다.
- 날짜별 시장은 `date=` 계약을 사용합니다. 기록 없는 날짜는 미수집 상태와 빈 결과를 표시하고 오늘 자료를 재사용하지 않습니다. 설명/선택창을 닫으면 초점이 돌아옵니다.
- 종목 요약 요청은 15초 제한과 코드 일치 검사를 사용합니다. 잘못된 종목 응답을 표시하지 않고 재시도할 수 있습니다. 갱신 중 304·503 이후에도 마지막 정상 자료를 보존합니다.
- 장중 간격 변경은 추가 API 호출 없이 원본에서 표시 시리즈만 바꿉니다. 차트·표·이전/다음 선택이 같은 점을 읽으며, 선택 날짜·공유 링크 우선순위·새로고침·리사이즈를 검증했습니다. 캔들/OHLC의 실제 봉 데이터와 일별·장기 차트는 이 표시 샘플링 대상이 아닙니다.
- 브라우저 회귀 13개 통과: 산업·오류 복구 3, 상세 갱신 1, 제목 스크롤 3, 과거 날짜 1, 모바일 읽기·관심 저장 4, 장중 간격 1. 모의 장애·뉴스·산업 자료는 테스트 fixture이며 실제 API 확보 증거로 사용하지 않습니다.
- 노드 검증: 기존 차트 기간 3, 차트 시리즈 3, 검색 프록시 7 및 신규 장중 구간 3 통과. 최근 추가 장중 구간·기간·시리즈 9개는 최종 변경 후 다시 통과했습니다.
- 최신 `tsc --noEmit`, 변경 주요 컴포넌트의 ESLint, `pnpm --filter @personal-fastgraphs/web build` 통과. 최종 생산 빌드는 Next.js 16.3.1, `/stocks`·`/icon.png` 생성까지 확인했습니다.

## 공개 v89 실제 응답 검증

2026-10-09 KST, `https://stocks.62-171-141-206.sslip.io/stocks`를 Chromium의 별도 검사 컨텍스트에서 확인했습니다. 이 검사는 mock 없이 공개 API를 호출했으며, 사용자가 선택한 인앱 브라우저의 디자인 증거를 대체하지 않는 추가 기능 검증입니다.

결과: **6개 검사 통과, 페이지 JavaScript 오류 0건, 검사 중 관찰한 API HTTP 실패 0건**. 근거는 `work/operations-20261008/public-v89-frontend.json`입니다. 최초 검사의 잘못된 CSS 선택자·분석 링크와 기본 5초 기다림은 검사 스크립트에서 수정한 뒤 재검사했으며, 해당 실패를 앱 오류로 보고하지 않았습니다.

| 실제 공개 검사 | 결과 |
|---|---|
| PC 시장 1440×900 | 실제 목록·뉴스 표시, 문서 가로 폭 1440px로 넘침 없음 |
| 기업 분석 EPS | 주가 `rgb(8,100,71)`, 비교선 `rgb(40,95,209)`와 범례 일치 |
| OCF·EBITDA 전환 | 각 지표를 선택하고 10배를 입력한 뒤 해당 실적 비교 범례가 표시됨. EPS로 대체되지 않음 |
| 모바일 390×844 | 첫 방문 5분 종가, 표 41구간과 1분 원본 200점을 서로 전환. 가로 넘침 없음 |
| 다크·가장 큰 글씨 | 설정 조작·반영, 종목 행 확장, 가로 넘침 없음. 심볼 밝기 필터 실제 적용 |
| 브랜드 자산 | PNG 로드 성공, 자연 너비 1254px |

추가 캡처: `public-v89-market-pc.png`, `public-v89-valuation-pc.png`(각 1440×900), `public-v89-price-mobile.png`, `public-v89-market-mobile-dark-large.png`(각 390×844). 마지막 모바일 시장 캡처는 지수 요약을 아직 불러오는 순간이므로 숫자 확보 성공을 증명하지 않습니다.

공개 자료의 별도 남은 문제: 당일 장중 시리즈는 최근 200개 원본(5분 표시 41구간)이며 하루 전체 확보를 뜻하지 않습니다. 종목 현재 자료는 10월 8일, 당시 분석의 마지막 과거 가격은 10월 2일로 각각 날짜가 표시됩니다. 수집·날짜 범위 보완은 부모 작업에서 처리하며 이 UI 검사로 완료 처리하지 않습니다.

## 의도적인 차이와 남은 게이트

- 시안에 있는 예시 기사·시장 수치·회사별 3개월 작은 차트를 만들어 넣지 않았습니다. 확인된 실제 조회 결과만 표시합니다. 각 행의 장기 그래프 일괄 조회가 연결되지 않은 구간은 거래량·정확한 숫자로 유지합니다.
- 기준 시안의 모바일 상단 메뉴를 중복 복제하지 않고 기존 하단 메뉴를 유지합니다. 48px 터치 영역과 18px 본문으로 인해 시안보다 첫 화면의 종목 수가 적습니다. 최소 한 종목과 다음 행 일부를 읽고 큰 조작으로 이어가도록 변경했습니다.
- 현재 로컬은 공개 API를 프록시합니다. v89 배포 전 새 `/news`·`/market-brief`·관계 API가 없던 공개 버전에서는 실제 오류·재시도 상태를 확인했고, v89에서 실제 시장 뉴스 표시를 확인했습니다. 관계·날짜별 자료의 전 종목 확보 여부는 별도 데이터 검증 항목입니다.
- Android Chrome·Samsung Internet·iOS Safari의 실제 기기 평가, 아버님의 과제 수행, 200% 확대와 모든 지정 폭, 전 종목 자료·장중 실제 수신·공개 성능·백업 복원은 이 프런트 검증에서 완료 처리하지 않았습니다.
- 남은 P3: 더 많은 실제 기사와 긴 이름 조합에서 보조 텍스트 간격을 관찰합니다. 디자인 맞추기를 위해 기능이나 상위 메뉴를 계속 추가하지 않습니다.

종료 판정: 발견한 P1·P2 화면 문제의 수정 후 증거를 확인했고 이 개편 범위의 스타일 변경을 중단합니다. 공개 장중 자료 범위와 최신 과거 가격, 실제 기기·아버님 과제·성능은 위에 남긴 별도 게이트입니다. 자료 문제가 해결되었다고 주장하지 않습니다.

---

## 2026-10-09 추가 검증: 전체 산업 탐색과 조회 상태

**로컬 구현·회귀 판정: passed. 공개 v92에서 첫 클릭 문제 1건을 발견했고, 아래 v93 한정 검사에서 수정 완료를 확인했습니다.** 기존 v89 화면 검증과 별도로 기록합니다. 앱 코드는 최종 생산 빌드 후 동결했습니다.

- 실제 공개 `/sectors/catalogue`는 `ready`와 분류 300개를 반환했습니다. 기존 순위 99개만 검색하던 제한을 제거하고, 산업 구역 안에 `순위 / 전체 산업` 전환과 전체 분류 검색·상위 경로·하위 산업 이동을 연결했습니다. 분류 목록에는 순위·등락률·거래대금을 만들어 넣지 않습니다.
- 산업 상세에서 설명·구성종목·상하위 분류를 열고 종목 조회 후 목록으로 돌아오면 검색과 계층 위치를 복원합니다. 기존 v1 저장 상태의 기간·순위 선택도 유지합니다. 상장 현황의 시장 선택은 코스피·코스닥으로 제한했습니다.
- 종목 유의사항은 정상 빈 응답에서 안내를 표시하지 않습니다. 오류·마지막 정상 자료·10초 이상 지연 상태에만 짧은 안내와 재시도를 제공하고 시세 화면과 마지막 정상 경고를 보존합니다. 시장 수급의 정상 빈 응답 `no_data`는 수집 중과 구분합니다.
- 인앱 로컬 `http://127.0.0.1:3248/stocks`에서 산업→전체 산업→반도체 검색을 실제 API로 확인했습니다. 전체 300개 중 검색 결과 11개, 반도체 하위 산업 6개와 설명·구성종목 진입이 보이며 가로 잘림이 없습니다. 근거: `work/operations-20261008/iab-sectors-v92-mobile.png`. PC 1440px의 같은 동선은 브라우저 회귀로 확인했습니다.
- 신규 검사 4개와 기존 산업 오류 복구 검사 3개, **서로 다른 총 7개 검사를 통과**했습니다. 신규 모의 데이터의 순위 밖 분류·계층 왕복·360/1440px·큰 글씨, 유의사항 `ready→error→stale→503→ready`·지연·재시도, 시장 `no_data`와 시장 선택 범위를 검사했습니다. 최초 실패 두 건은 검사 선택자·비동기 대기 누락을 수정해 각각 재검사했으며, 단일 최종 실행에서 7개를 모두 실행했다고 주장하지 않습니다.
- `tsc --noEmit`, 변경 컴포넌트 ESLint, `pnpm --filter @personal-fastgraphs/web build` 통과. 최종 빌드는 Next.js 16.3.1, `/stocks`·`/icon.png` 생성과 exit 0을 확인했습니다. 이후 앱 코드 변경은 없습니다.

변경 앱 파일은 `toss-sectors.tsx`, `market-data.tsx`, `market-reference.tsx`, `app/stocks/market.css`이며, 신규 회귀는 `apps/web/tests/market/sector-catalogue.spec.ts`입니다. 이 추가 검증은 실제 휴대폰·아버님 평가나 전체 자료 확보의 완료 판정이 아닙니다.

### 공개 v92 한정 검사

2026-10-09 01:56~02:02 KST에 공개 health의 `status=ok`, `release=20261009-sector-history-v92`를 확인한 뒤 격리 Chromium 컨텍스트에서 실제 API로 검사했습니다. 사용자의 기존 탭·저장값은 변경하지 않았습니다.

- **실제 산업 동선 통과:** 전체 분류 300개와 순위 99개를 대조하고 순위 밖 `항공사` 검색을 확인했습니다. `반도체→반도체부품소재→삼성전기→목록 복귀` 후 계층과 전체 산업 선택이 복원됐습니다. 공개 상장 시장 선택도 코스피·코스닥만 표시했습니다.
- **기하 검사 24개 통과:** 시장·시세·기업 분석을 각각 360·390·430·768·1024·1280·1440·1920px에서 검사했습니다. 페이지 전체 가로 넘침, 검사 대상 주요 버튼의 가로 잘림, 종목 제목과 차트의 겹침은 없었습니다. 시세·분석은 스크롤 후에도 제목이 차트를 덮지 않았습니다. 이는 지정 요소의 기하 검사이며 모든 조작·실제 기기·200% 확대 평가를 대신하지 않습니다.
- **P2 첫 클릭 유실:** 초기 `document.readyState=interactive`, 탐색 상태 복원 전 `산업` 버튼이 활성화돼 있었습니다. 실제 클릭 이후 10초가 지나도 업종이 유지됐고, 복원 뒤 두 번째 클릭은 정상 작동했습니다. 초기 클릭 전후 상태와 요청 시각은 `public-v92-frontend-recheck.json`의 `clickEvidence`·`network`에 남겼습니다. 재검사 함수의 `passed`는 진단 절차 종료를 뜻할 뿐, 그 안의 `firstResult:false`를 기능 통과로 해석하지 않습니다. 앱 수정·후속 빌드 전까지 이 문제를 해결 완료로 처리하지 않습니다.
- 최초 시장 기하 검사는 숨겨진 모바일 행을 선택한 검사 오류였습니다. `:visible` 선택자로 실패 항목만 재검사해 통과했습니다. 시세·분석의 통과 검사는 반복하지 않았습니다. 최초 산업 실패는 단순 검사 오류로 분류하지 않고 위 실제 초기 조작 문제로 기록했습니다.
- 검사에서 페이지 JavaScript 오류와 API HTTP 실패는 관찰되지 않았습니다. 이는 응답의 전체 자료 확보를 증명하지 않습니다. 유의사항 강제 오류·지연은 공개 서버를 방해하지 않고 로컬 회귀 증거를 사용했습니다.

증거: `work/operations-20261008/public-v92-frontend.json`, `public-v92-frontend-recheck.json`. 실제 캡처 `public-v92-analysis-1440.png`(1440×900), `public-v92-analysis-390.png`(390×900)를 직접 열어 주식담 로고·초록 가격/청색 비교선·목차·조작의 줄바꿈을 확인했습니다. 모바일 캡처에서 차트는 화면 아래에 있으므로 한 장으로 차트 전체를 검증했다고 표현하지 않습니다.

### v93 준비: 초기 클릭 P2 수정

시장 탐색과 산업 탐색의 상태 복원이 끝나기 전에는 native `fieldset disabled`와 `aria-busy=true`를 사용합니다. 서버가 처음 제공하는 HTML부터 비활성 상태이므로 아직 연결되지 않은 클릭을 받을 수 있는 것처럼 표시하지 않습니다. 복원 후에는 즉시 활성화하며 기존 URL·session 저장 우선순위와 계산·조회 동작은 변경하지 않았습니다. fieldset의 테두리·여백·최소 폭을 제거해 기존 배치를 유지했습니다.

- **실제 지연 재현:** 서버 HTML과 CSS는 그대로 받고 실행 JS 청크만 보류했습니다. 360·1440px에서 초기 비활성 상태, 화면에 보이는 버튼의 물리 클릭 차단, 저장된 `테마` 탭 복원, 이후 산업 첫 클릭, 저장된 `상승률순·1년` 복원을 확인했습니다. 앱에 테스트 전용 대기 코드를 넣지 않았습니다.
- **회귀:** 신규 초기화 2개, 전체 산업·유의사항·범위 4개, 산업/상세 복구 3개가 통과했습니다. 기존 키보드 검사는 준비 전 native disabled 버튼에 프로그램적으로 focus하던 부분을 준비 후 focus하도록 변경해 다시 통과했습니다. 신규 지연 검사는 준비 전 상태를 별도로 검증하므로 문제를 대기 시간으로 숨기지 않습니다.
- **품질 검사:** TypeScript, 변경 컴포넌트·신규 검사 ESLint, 최종 생산 빌드 exit 0 통과. 앱 코드는 빌드 이후 동결했습니다. 초기에 잘못 선택한 테스트 설정의 API 기동/포트 실패는 실행 설정 오류로 수정했으며 앱 결함으로 집계하지 않았습니다.
- **화면:** `v93-hydration-initializing-360.png`, `v93-hydration-ready-360.png`, `v93-hydration-initializing-1440.png`, `v93-hydration-ready-1440.png`를 `work/operations-20261008/`에 보존했습니다. 모바일 준비 전과 PC 준비 후 화면을 직접 열어 비활성 표현과 기존 배치를 확인했습니다. fieldset의 border/margin/padding은 모두 0px, 지정 두 폭에서 페이지 넘침 없음입니다. 순위 숫자는 이 검사에서 fixture이며 실제 자료 확보의 증거가 아닙니다.

변경 앱 파일은 `market-explorer.tsx`, `toss-sectors.tsx`, `app/stocks/market.css` 3개입니다. `exploration-hydration.spec.ts`를 추가하고 기존 `sector-recovery.spec.ts`의 키보드 준비 상태 검사만 보완했습니다. **로컬 P2 수정·검증 완료, v93 공개 반영은 부모 배포 후 별도 확인합니다.**

### v93 공개 확인: 초기 클릭 P2 종료

공개 health에서 `20261009-first-interaction-v93`·`status=ok`를 확인했습니다. 같은 공개 버전에서 **JS 청크를 지연한 360·1440px 두 경우와 지연 없는 첫 조작 한 번**, 총 3개 동작 검사가 모두 통과했습니다. 이전 24개 폭·전체 동선은 반복하지 않았습니다.

- 지연 중 버튼이 native disabled이며 `aria-busy=true`이고, 클릭해도 저장 상태를 덮어쓰지 않습니다.
- 스크립트 실행과 session 복원 후 저장된 테마·상승률순·1년을 유지하고 산업 첫 활성 클릭이 반영됩니다.
- 지연하지 않은 새 컨텍스트에서도 산업 클릭 **1회**로 정상 전환됐습니다. 페이지 JavaScript 오류 0건입니다.
- 두 폭 모두 fieldset의 테두리·여백·패딩 0px, 페이지 가로 넘침 없음입니다. `public-v93-initializing-360.png`와 `public-v93-ready-1440.png`를 직접 열어 준비 전 비활성 표현과 준비 후 기존 배치를 확인했습니다. 준비 후 캡처에는 아직 순위 요청이 진행 중이므로 이 사진을 실제 순위 확보 완료 근거로 사용하지 않습니다.

기록: `work/operations-20261008/public-v93-hydration.json`. 캡처: 같은 폴더의 `public-v93-initializing-360.png`, `public-v93-initializing-1440.png`, `public-v93-ready-360.png`, `public-v93-ready-1440.png`. API 모의 응답 없이 실제 공개판을 사용했으며, 독립 브라우저 컨텍스트를 모두 종료했습니다. 이 P2 항목은 **구현·브라우저 검증·공개 반영 완료**로 종료합니다. 실제 기기·아버님 사용 평가 및 제품 전체 데이터·운영 판정은 별도입니다.

---

# LUXON Design QA (과거 기록)

Status: pre-Claude-design baseline
Audit date: 2026-08-16

This is the measured baseline for the current implementation. It is intentionally
`blocked`: the backend/data contracts and working terminal foundation are ready,
but the requested FAST Graphs-familiar, FnGuide-dense frontend redesign has not
yet passed the Claude Design implementation contract.

## Scope and routes

Audited routes and states:

- `/terminal?ticker=AAPL&tab=Historical`
- Historical metric menu, 4Y period, chart settings, and Data Audit transition
- Consensus `missing_source` state
- desktop 1440 x 900
- mobile 390 x 844

Canonical target routes for the next implementation pass:

- `/terminal` for search and resume
- `/company/[id]/graph` for Historical Graph
- `/company/[id]/snapshot`
- `/company/[id]/financials`
- `/company/[id]/forecast`
- `/company/[id]/consensus`
- `/company/[id]/peers`

## Reference evidence

Public references:

- FAST Graphs Historical Graph:
  <https://docs.fastgraphs.com/en/articles/9419962-historical-graph>
- FAST Graphs Forecasting Charts User Guide:
  <https://docs.fastgraphs.com/en/articles/13577168-forecasting-charts-user-guide>
- FnGuide Company Guide information architecture:
  <https://wcomp.fnguide.com/Help/Guide?cmp_cd=0101N0>

The public Historical reference and current LUXON implementation were captured
at the same viewport and combined for visual comparison. Audit captures stay
outside the repository under `<TEMP>/luxon-design-audit-2026-08-16/`; they are
not product assets and must not be committed.

## Viewports and states

Checked directly:

- desktop Historical default
- metric menu open
- 4Y range selected
- chart settings expanded
- Data Audit navigation
- Consensus missing-source state
- mobile Historical layout

The AAPL path was explicitly `fixture_non_production`. This visual audit does not
prove live KR data readiness.

## Typography

Current text is legible and appropriately dense in the main chart surface.
However, the number of stacked headers, tab rows, controls, and status labels
reduces scan speed. The Claude pass must preserve readable body text while
compressing structural overhead.

## Spacing and alignment

Passed foundation:

- Historical metric, period, and chart-settings controls now share a compact
  desktop band.
- The chart, range strip, and evidence rail use a coherent grid.

Blocking gaps:

- The plot receives too little horizontal share relative to the facts rail.
  Target comparison starts at plot 78-82% and rail 18-22% at 1440px.
- Non-Historical workspaces inherit Historical controls and vertical overhead.
- Major content is visually reordered with CSS while DOM order stays different.

## Color and chart semantics

Passed foundation:

- price, fundamental, fair-value, normal-multiple, and forecast layers have
  distinct semantics;
- metric menu clearly marks locked/source-required values;
- missing Consensus does not invent numbers.

Blocking gap:

- `fixture_non_production` can coexist with a positive green source-backed
  signal in the current shell. These states must be mutually exclusive across
  badges, legends, exports, and assistive text.

## Borders, radii, elevation

The current flat bordered surfaces are compatible with a dense terminal. The
next pass should reduce redundant card shells and reserve elevation for actual
drawers, menus, and overlays. This is P1 polish after the state and IA blockers.

## Assets and brand/IP boundary

Passed:

- LUXON uses its own brand assets, code, chart renderer, tokens, copy, data
  model, and deterministic formulas;
- no FAST Graphs/FnGuide assets are shipped;
- authenticated commercial-surface automation and scraping tooling were removed;
- reference capture is limited to manual public-document review outside Git.

## Interactions

Passed directly:

- metric menu opens and exposes availability state;
- period selection updates the displayed range;
- chart settings open;
- Data Audit navigation changes the route state;
- Consensus fails closed;
- browser console showed no warning or error during the audited flow.

Blocking gaps:

- active navigation can move between two tab rows;
- chart settings appear in more than one surface;
- redirect-only company routes are not meaningful route shells;
- `/terminal` is still the monolithic company workspace instead of focused
  search/resume;
- one broad endpoint fanout can turn a single core request failure into a global
  fallback.

## Data states and source_trace

Passed foundation:

- shared response states include ready, partial, stale, configured,
  fixture_non_production, missing_source, missing_contract, missing_key,
  rate_limited, and upstream_error;
- Consensus and Peers fail closed;
- provider credentials are represented as configuration state, not guessed;
- visible research contracts include source-trace details and recovery copy.

Remaining visual gate:

- every workspace must render its own loading, empty, stale, and recovery state
  independently;
- positive source language must be impossible when the actual envelope is
  fixture or unavailable.

## Accessibility

Passed foundation:

- key controls have accessible names;
- selected control state is exposed;
- chart-year keyboard handling includes Arrow keys, Home, End, and Shift+Enter.

Blocking gaps:

- DOM and visual order differ for major regions;
- two-row tab overflow harms keyboard location stability;
- a complete screen-reader, zoom, forced-colors, contrast, and focus-order pass
  remains required.

## Responsive behavior

Blocking findings at 390 x 844:

- both tab strips require large horizontal scrolling;
- fixed evidence and bottom navigation consume excessive chart height;
- desktop information architecture is compressed rather than reduced;
- the chart lacks a strong visible cue for off-screen range and selection.

The canonical company default remains Graph at every viewport. Snapshot may be
the first prominent mobile navigation destination, but viewport changes must not
silently change the route.

## Automated verification

Current implementation verification:

- Python: 377 passed
- focused API/Ruff checks: passed
- TypeScript type-check: passed
- Next.js production build: passed, 10 routes generated
- ESLint: 0 errors, 10 pre-existing warnings
- Playwright: 10 passed
- production dependency audit: 0 known vulnerabilities
- Docker Compose model validation: passed with a validation-only password
- PowerShell parser validation: 12 scripts passed
- GitHub workflow guard: all actions SHA-pinned, read-only permission, no direct
  input/secret interpolation in run blocks
- public snapshot secret/PII scan: no real secret, personal path, private Figma
  identifier, or authenticated-capture reference found

## Known external gates

- operator API keys are not configured in the repository;
- a live source-backed KR E2E was not run in this audit;
- Docker Desktop was unavailable for an actual image build/run;
- protected Render/Vercel/managed-Postgres deployment was not performed;
- Claude Design has not yet implemented and re-audited the P0/P1 frontend pass.

## Residual P2 items

Only after the P0/P1 gates close:

- minor typography rhythm;
- secondary transition timing;
- decorative chart/export polish;
- optional dark-theme refinement outside changed components.

## Blocking acceptance list

1. Enforce fixture/source-backed state mutual exclusion.
2. Make DOM order equal visual order for major regions.
3. Replace moving two-row navigation with the fixed Graph/Snapshot/Financials/
   Forecast/Consensus/Peers/Performance/More model.
4. Give each route independent data loading and recovery boundaries.
5. Replace redirect-only company paths with meaningful server route shells.
6. Keep Historical controls on Graph and route-specific chart controls only.
7. Provide one canonical chart-settings surface.
8. Remove page-level horizontal overflow at 1440, 1024, and 390px.
9. Complete same-viewport visual comparison after fixes.
10. Complete keyboard, screen-reader, zoom, contrast, and mobile checks.

final result: blocked
blocker: Claude Design must implement and verify the ten blocking acceptance items above before the requested frontend can be called complete.
