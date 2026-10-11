# 최초 주식 단위 근거 승격

이 경로는 `share-basis-bootstrap:<종목>`의 발견 결과를 자동 승인하지 않는다. 검토자가 원문을 확인해 작성한 명시적 결정만 받아 기존 근거가 없는 보통주의 **무분할 구간**을 등록한다. 분할·병합·재편 포함 구간과 우선주는 기존 전용 검토 대상으로 남는다.

## 검토 입력

`services.market_dashboard.share_basis_promotion.queue_initial_review(store, code, review)`를 내부 작업에서 호출한다. 공개 HTTP 경로는 없다. `review`는 다음을 모두 포함한다.

- `status: reviewed`, `share_class: ordinary`, 정확한 `security_code`·`company_id`, `reviewer`·`reviewed_at`·판단 이유 `reason`.
- `evidence`: 기존 share-basis 형식의 검증된 무분할 구간. `verified`, `no_split_interval`, `basis_id`, 시작·종료일, 발행회사 원문의 `source_trace`.
- `input_fact_ids`: 검토한 회사의 실제 EPS 입력 ID 전체. 현재 FactStore의 귀속이익·가중평균 주식 수·공시 EPS를 재대조한 결과와 일치해야 한다.
- `proofs`: `issuer_share_history`, `ordinary_share_identity`, `filing_unit_alignment` 세 구분. 각각 `raw_source`, 원문 `sha256`, `source_url`, 검토한 `excerpt`를 포함한다. 원문·메타데이터 URL·해시를 다시 확인한다. 구절 의미에 대한 검토는 별도의 명시적 판단이며 자동 생성하지 않는다.
- `disclosure_window`: 회사 ID, `begin`(YYYYMMDD), `end`(YYYY-MM-DD), 기존 `share_basis_worker.validate_page` 형식의 `pages`. 전체 구간의 정정 포함 DART I 유형·전체 페이지·원본 해시·개수와 회사가 일치해야 한다.

공시 창에 주식 단위 변경이 있으면 승격하지 않는다. 유상증자·자사주 등의 주식 수 변경이 있으면 기존 `share_basis_review_store`에 해당 공시의 명시적 검토가 있어야 한다. 금액·기간·EPS 입력이나 회사 연결이 바뀌면 기존 결정을 재사용하지 않는다.

## 재개와 보존

검토 입력은 불변 `initial-share-review:<종목>:<hash>`와 작업 큐에 저장된다. 기존 작업을 바꿀 때 `replaces`에 정확한 이전 검토 ID가 필요하다. 기존 원본·결정은 보존한다.

기존 주식 단위 작업자가 공간 기준을 통과할 때 한 번에 한 건을 검사한다. 등록 직전 원문과 입력 버전을 다시 확인하고 조건부 최초 INSERT를 수행한다. 기존 share-basis는 덮어쓰지 않는다. 실패·입력 변경은 `review_required`로 남기고 자동 반복하지 않는다.

이 큐의 구현·테스트 통과는 실제 기업 검토 완료나 전 종목 TTM 확보를 뜻하지 않는다. 실제 운영 등록 수, 연속 기간, 주가 비교 가능 구간은 별도 기록한다.

## Toss 과거 요청의 공정 배분

공통 요청 게이트는 **같은 API 그룹**에서 최신·사용자 조회와 과거 요청이 동시에 대기하면 네 차례 중 최소 한 차례를 과거 요청에 제공한다. 빈 대기열의 몫은 즉시 다른 요청이 사용한다. 비활성 시간·인증 실패·429 대기 중 기회가 적립되지 않으며 `Retry-After`가 우선한다.

`toss-api:<경로>`의 내부 metrics에 60초 admission 수·대기열, 실제 `requests_by_workload`를 기록한다. admission은 성공한 자료 수가 아니며 서로 다른 API 그룹까지 합친 무조건 25% 호출 비율을 뜻하지 않는다.
