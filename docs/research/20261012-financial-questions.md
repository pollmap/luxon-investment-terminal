# 이익과 현금흐름: 회사 전체 금액의 읽기 계약

2026-10-12 코드 감사와 로컬 구현 기록입니다. 새 수집·원문 다운로드·운영 DB 쓰기·검증 상태 승격·VPS 작업을 실행하지 않았습니다. 아래 구현과 fixture 검증은 전 종목 자료 확보나 공개 반영을 뜻하지 않습니다.

## 재사용한 입력과 계산

`build_financial_questions(analysis_view, basis='CFS', *, company_id=None, dated_facts=())`는 순수 함수입니다. 기존 `official_views`의 분석 결과에서 `ttm[basis].rows`를 읽으며 Store를 만들거나 캐시를 쓰지 않습니다. 선택적으로 전달받은 좁은 CAPEX 사실에는 기존 `trailing_twelve_months` 계산기를 사용합니다. 문서를 다시 파싱하거나 전체 가격·재무 저장소를 조회하지 않습니다.

| 결과 | 주 입력 | 판정 |
|---|---|---|
| 매출액·영업이익·순이익·영업현금흐름 | 기존 검증 기간 TTM 요약 | 실제 시작/종료·350~380일·연결/별도·회사·KRW 금액·회사 차원·공개일 근거 확인 |
| 유형·무형자산 취득 지출 | 실제 CAPEX 표준 XBRL 계정의 연속 네 분기 | 양쪽 계정 모두 필요. 없는 무형 취득을 0으로 대체하지 않음 |
| 잉여현금흐름 | 동일 기간 OCF − 위 취득 지출 | 실제 입력 범위·부호·회사 전체 범위 일치 필요 |
| 순이익 대비 영업현금흐름 | OCF / 순이익 × 100 | 동일 회사 전체 모집단이고 순이익이 양수인 경우만 계산 |
| 순이익률·영업이익률 | 순이익 또는 영업이익 / 매출 × 100 | 동일 기간·회사 전체 범위, 양수 매출 필요 |

금액 계산은 Decimal이며 `exact_value` 문자열을 보존합니다. 화면용 `value`는 숫자입니다. 음수 OCF와 실제 0인 금액도 사실 그대로 보존합니다. 순이익이 0 이하인 경우 현금 전환 비율만 계산하지 않습니다.

EPS의 귀속이익·가중평균 주식 수·분할 기준과 이 표의 회사 전체 금액은 별개입니다. 이 함수는 EPS·주당 현금흐름을 만들지 않으며, 주식 단위가 없다는 이유만으로 검증된 회사 전체 금액을 숨기지 않습니다. 그룹 OCF에 지배주주 귀속 순이익을 나누지 않습니다. 기본 `ProfitLoss`·`CashFlowsFromUsedInOperatingActivities` 등 실제 표준계정 또는 독립적으로 검증된 `entity_scope=company_total`만 비율의 모집단 근거로 사용합니다. 이름만 '당기순이익'인 기존 요약은 금액을 보존하되 회사 전체 범위가 불명확하면 비율은 보류합니다.

## 응답과 근거

최상위 필드는 `basis / company_id / scope / frequency / period_start / period_end / rows / comparisons / availability / status / formula_version`입니다. `scope`는 `company_financial_totals`, `per_share_status`는 `not_calculated`입니다.

각 결과는 `value / exact_value / unit / status / reason / reason_label / input_fact_ids / available_at / source_trace / formula / formula_version`을 포함합니다. 파생 결과는 입력의 공개일 중 가장 늦은 시점을 사용합니다. `publication_time_unverified` 플래그가 붙은 수집시각을 공개시각으로 인정하지 않습니다. 실제 원문 해시와 입력 위치가 없거나 공개일이 확인되지 않으면 `null`과 사유를 반환합니다.

기존 TTM 요약은 원래 DB `fact_id`를 항상 전달하지 않습니다. 실제 ID가 있으면 그대로 보존하고, 없는 경우에는 원문 해시·회사·계정·context/기간·공시 ID를 연결한 `source-observation:` 위치 식별자를 사용합니다. 이 경우 `source_trace.inputs[].input_kind=source_observation`으로 명시합니다. 생성한 위치 식별자를 운영 DB의 실제 행 ID처럼 표시하지 않습니다. 전체 원문이나 인증값은 응답에 넣지 않습니다.

CAPEX·FCF·비율은 `required_inputs / missing_inputs`도 제공합니다. 최신 기간과 다른 OCF나 CAPEX가 있더라도 이전 값으로 최신 기간의 빈칸을 메우지 않습니다. 같은 공개 시각에서 금액이 충돌하면 임의로 선택하지 않고 `conflicting_inputs`를 반환합니다. 정정의 공개시각이 더 늦은 경우 해당 버전을 사용하고 근거를 보존합니다.

공개 응답의 원문 링크는 14자리 접수번호가 있으면 DART 문서 주소로 재구성합니다. 그렇지 않으면 공식 DART·KIND HTTPS 호스트에서 인증값 없는 query만 허용합니다. `crtfc_key`, 토큰·비밀번호·서명 등의 query는 출력하지 않습니다. 원본 trace는 그대로 보존합니다.

## 기존 기능 상태의 구분

| 묶음 | 정의·계산 코드 | 입력 검증 | 기존 화면 연결 |
|---|---|---|---|
| 기본 EPS·TTM | 귀속이익·가중평균 주식 수·EPS 대조·분할 게이트 | 기업별 검증된 구간만 | 실적과 주가·공시 EPS 표 |
| 희석 EPS | 공시값 읽기 | 실제 희석 공시 입력만. 희석 TTM 재계산은 없음 | 지표 선택에 연결 |
| OCF·FCF·EBIT·EBITDA·매출/주 | 구분된 산식과 TTM | 계정·회계 범위·주식 수·분할 기준 필요 | 실제 준비된 지표만 비교선 |
| FCFE | 현금흐름 정책 검증이 있는 산식 | 차입·상환·이자/리스 분류 검토 필요 | 실제 입력이 있을 때만 |
| 조정 EPS·FFO·AFFO | 독립된 정의 | 공시 조정 내역의 별도 실제 입력 필요 | 정의가 있다고 사용 가능하지 않음 |
| 회사 전체 이익·현금흐름 | 이번 순수 계산 함수 | 기존 TTM 요약의 근거를 추가 확인 | root의 API·화면 연결은 별도 작업 |
| 현금·부채·유동성·이자보상 | DART 전체 계정·안정성 지표 표 있음 | 통일된 잔액·차입금 읽기 모델은 아직 없음 | 별도 건전성 요약 화면 없음 |
| 예상실적 | 연간 추정 표 | 기관 수·추정치 이력·발표 전 스냅샷 확보는 별개 | 기존 표만. 서프라이즈 계산 없음 |
| 동종 비교 | 같은 업종·시가총액 정렬 | 동일 회계기간 재무 비교는 미구현 | 현재가·등락·시총 목록 |
| 배당·주주 | 필드별 날짜 상태·공시 내역 | 전 종목 지급일과 이력 확보는 별개 | 배당·주주·기업 일정 |

## 검증과 한계

새 fixture는 회사 전체 금액과 주당 지표 분리, 혼합 회사/회계 기준/기간/통화/단위/차원, 지배주주 이익, 결측·0·적자, 실제 CAPEX 양쪽 계정·부호·미래 공개일, 정정·충돌과 정확한 Decimal 보존을 검사합니다. `fixture_non_production` 자료이며 실제 회사 확보율로 사용할 수 없습니다.

기존 TTM 요약의 출력에는 개별 원문의 모든 context 값이 포함되지 않았습니다. 추가 수정은 실제 입력의 `input_period_start / input_period_end / input_company_id / input_fact_ids / input_published_at`을 읽기용 source trace에 보존합니다. 원본·원문 SHA·기존 quality flag는 변경하지 않습니다. 같은 종료일의 회사·공개일·금액 단위·차원 증거가 있는 입력을 undated IS 요약보다 먼저 선택합니다. 공개일이 없거나 접수번호만 있으면 새 공개일을 만들지 않습니다.

질문 함수는 명시 input context의 회사와 각 종료일 이후 공개일을 확인합니다. 앞 분기가 끝나기 전에 공개한 것으로 표시된 입력은 마지막 공시의 날짜가 정상이어도 통과하지 않습니다. 원래 요약에 context가 없는 경우는 기존 TTM 기간 게이트와 합산 종료일 이후 공개시각을 유지하며, 원문 검증을 대신한 것으로 표현하지 않습니다. 회사/기간/공개시점의 운영 원본 대조와 API·표·그래프 일치는 별도 공개 검증이 필요합니다.

## 실제 공개 자료와 코드 연결의 부족: 2026-10-13 KST 추가 기록

기존 공개 `/securities/005930/financials/ttm`, `/securities/000660/financials/ttm`을 각 한 번 조회했고 두 응답 모두 HTTP 200이었습니다. 두 회사 모두 2025-07-01~2026-06-30의 매출·영업이익·순이익 3행만 최신 TTM 요약에 포함됐습니다. `company_id`는 빈 문자열이고 입력의 공개시각은 null, `publication_time_unverified` 상태였습니다. 최신 OCF 입력은 반환되지 않았습니다. 새 순수 함수의 금액·비율은 이 증거로 검증 완료가 될 수 없어 null과 사유를 반환합니다. 원본 응답과 순수 함수 결과는 ignored `work/financial-questions-public-v134.json`에 보존했습니다.

삼성전자 2025 연결 연간 재무도 한 번 조회했고 HTTP 200·ready, 현금흐름표 42행이 저장돼 있었습니다. 따라서 '현금흐름 원문 전혀 없음'으로 분류하지 않습니다. 해당 응답에는 실제 기간 시작·종료가 없고 보고기간은 '제57기', 공개시각은 null입니다. 보고서 코드만으로 1월~12월 기간을 만들어 TTM 입력으로 승격하지 않습니다. ignored `work/financial-questions-annual-public-v134.json`에 보존했습니다.

코드상 회사 ID 누락은 `DartPeriodCollector`가 정규화 사실 테이블에만 법인 ID를 넣고 official의 `period_facts`에는 원래 ID 없는 목록을 저장한 데서 발생했습니다. 수정은 검증된 기존 corporation mapping을 두 저장 경로에 동일하게 보존합니다. 기존 ID 없는 스냅샷의 일괄 재분류는 수행하지 않았습니다. 공개일은 major-account 원문에 실제 `rcept_dt`가 없으면 계속 미검증입니다. 보존된 DART 공시 목록에서 회사·접수번호·원문 SHA를 대조한 실제 날짜 연결이 별도로 필요합니다.

기존 분석 projection 캐시는 버전을 올려 새 읽기 구조로 재구축해야 합니다. 캐시 재구축·공개 반영·실제 OCF 연결을 fixture 통과로 대신하지 않습니다.
