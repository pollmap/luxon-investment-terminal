# v52 — 과거 IFRS 입력 인식과 단위 오류 분리

공개 버전 `20261004-ifrs-legacy-v52`. 주식 기준 의존 캐시·토스 수집·배당 연결·프런트·사용자 데이터는 유지했다.

## 확인한 문제와 변경

운영 사실 DB에서 구형 `http://xbrl.iasb.org/taxonomy/2010-04-30/ifrs` 기본 EPS 1,019건, 8개 회사가 확인됐다. 이전 계산기는 `xbrl.ifrs.org/` 문자열만 허용해 이러한 사실을 계정 인식 단계에서 조용히 건너뛰었다.

공통 full IFRS 네임스페이스 판별기를 추가하고 기본/희석 EPS, 가중평균 주식 수·귀속이익, 현금흐름·EBITDA 등의 기존 계정 인식에 연결했다. 구형 2010 주소는 확인된 정확한 주소만 허용한다. 현재 full IFRS와 이전 ifrs 주소를 인식하되 SME·management commentary·기업 확장 계정·비슷한 호스트를 표준 IFRS 계정으로 취급하지 않는다. 검토된 기업 주석 매핑 경로는 유지한다.

근거: [IFRS Taxonomy Architecture](https://www.ifrs.org/content/dam/ifrs/standards/taxonomy/general-resources/ifrs-taxonomy-architecture/ifrs-taxonomy-architecture-2020.pdf), [2010 IFRS 기반 분류체계의 공식 기술 안내](https://www.cmfchile.cl/sitio/xbrl/html/archivos_tax2010.php). 이는 구형 네임스페이스의 기술 확인이며 개별 한국 기업 숫자의 정확성을 대신 검증하지 않는다.

분석 투영 버전은 8에서 9로 올려 기존 캐시도 새 인식 규칙으로 재검사한다. 원본 사실의 단위·값·문서·ID를 변경하지 않는다.

## 실제 자료 대조 결과

8개 회사의 기본+희석 EPS 원본 2,002건을 대조했다. 모두 plain shares 단위로 저장돼 있었으며, 기본 EPS 1,019건은 수정 이후 `per_share_unit_unverified`로 명확하게 분류됐다. 수정 전에는 이 자료를 인식하지 않아 제외 사유조차 없었다.

이번 변경으로 해당 구간의 새 검증 EPS나 TTM이 생성되지는 않았다. plain shares를 원/주로 바꾸거나 값만 보고 통화를 추정하지 않았다. 원본 주석·단위 선언 검증 또는 검증된 귀속이익/가중평균 주식 수 입력이 필요하다. 희석 EPS는 별도 metric 요청에서 검증하며 기본 EPS의 분모 변경으로 생성하지 않는다.

## 검증

- 계정 인식·EPS·현금흐름·분할 기준 캐시·분석 시계열 테스트 52개 통과.
- 구형/신형 동일 수치의 계산 일치, plain shares EPS 거부, SME·유사 호스트·미확인 구형 주소 거부 검증.
- 현금흐름/주·FCF·EBIT·EBITDA·매출/주의 각 산식은 유지되며 구형 지표를 EPS로 대체하지 않는다.
- 공개 health의 v52·수집 중 상태 확인. 삼성전자 지표와 SK하이닉스 EPS 분석 HTTP 200. 기존 SK하이닉스 비교선 availability 유지.
- 인증·새 DART 호출·수집 커서 초기화·원본 재작성 없이 저장된 사실만 검사했다.

근거 파일: `work/remaining-v52/before.json`, `recognition-before.json`, `recognition-after.json`, `tests.xml`, `public.json`. 핵심 코드: `taxonomy.py`, `eps_calculation.py`, `per_share.py`, `fundamental_metrics.py`, `official_views.py`, `tests/test_taxonomy.py`.

## 남은 범위

과거 EPS 단위 검증·TTM 및 주식 기준의 회사/기간 확대, 전체 과거 수집과 404 보완, 배당 기업행사·재투자, 실제 장중 체결/호가와 복구, 공개 100동시 성능, 화면·아버님 실사용 검증은 남아 있다. 새 수치가 없다는 사실을 숨기거나 계정 인식 수정으로 전체 재무 확보를 완료 처리하지 않는다. 이번에는 공개 부하 시험을 반복하지 않았으며 기존 p95 목표 미달 판정은 유지한다.
