# 데이터 작업 연결과 공간 제한

2026-10-12 로컬 v133 후보 코드 점검입니다. 이 문서를 작성하면서 운영 DB 쓰기, 공급자 호출, 새 수집, 검증 상태 승격을 실행하지 않았습니다. 아래 운영 수치는 날짜가 붙은 기존 기록이며 현재 확보율로 재사용하지 않습니다.

## 구현한 연결

- `share_basis_bootstrap._archived_index_reports`: 재무 요약에 없는 연간·반기 보고서를 저장된 DART 목록 원문에서 찾습니다. 공식 요청 URL·회사·접수번호·제목·공개일·SHA가 일치해야 하며 최대 4건입니다. 분기 월만 보고 Q1/Q3 보고서 코드를 추측하지 않습니다.
- `prepare_bootstrap → xbrl-bootstrap-intent → service_xbrl_intent`: 기존 XBRL 슬롯 한 번에 최대 1건을 전달합니다. XBRL 루프는 5회 중 1회 이 큐를 먼저 확인합니다. 성공한 수집은 EPS 대조·주식단위 검토·TTM 완성으로 승격되지 않습니다.
- `corporate_events_loop`: 기존에 없던 `collection_capacity(store, 'corporate-events')`를 추가했습니다. 제한 중에는 60초 대기하고 신규 문서 수집·대상 커서 진행·최초 관계 정리 쓰기를 보류합니다. 재개 후 최초 정리는 한 번만 하고 기존 15초 수집 주기로 돌아갑니다. 저장된 자료를 읽는 공개 API는 변경하지 않습니다.

최초 입력 연결 8 fixture 사례는 모두 통과했습니다(`work/initial-share-index-v133.xml`, 실패·오류·건너뜀 0). 원문 해시에서 큐를 거쳐 한 슬롯씩 전달되는 경로와 중복 접수번호 처리 방지를 검사했습니다. 이 결과는 실제 LG전자 누락 보고서가 운영 큐에 등록됐다는 증거가 아닙니다.

기업행사 공간 제한의 신규 3사례와 기존 저장공간 프로필 21사례도 24개 모두 통과했습니다(`work/corporate-capacity-v133.xml`). 최초 제한·제한 후 재개·다시 제한된 뒤 재개할 때 수집과 커서가 멈추고 정상 자료가 보존되는지 fixture로 검증했습니다. 실제 운영 재개 검증은 별도입니다.

## 디스크 95%에서의 정확한 동작

| 경로 | 공간 검사와 작업 한도 |
|---|---|
| `share_basis_review_loop` | `share-basis` 검사 후에만 최초 입력 준비·검토 대기열 처리·기존 기준 연장. 한 회전당 최초 후보 1개, 60초 간격 |
| `xbrl_loop` | `xbrl` 검사 후에만 intent와 일반 수집. 제한 중 60초 대기, intent는 한 슬롯당 최대 1개 |
| `TossBackfill.step` | 첫 부분의 `backfill_capacity` 검사에서 제한되면 기존 커서를 보존하고 반환 |
| `dated_accounts_loop` | `dated-accounts` 검사 후 기간 재무 수집 |
| `corporate_events_loop` | 이번 수정의 `corporate-events` 검사 후 기업행사 수집 |

`storage_budget.py`에는 두 고정 프로필이 있습니다.

| 프로필 | 새 중단 조건 | 이미 중단된 큐의 재개 조건 |
|---|---|---|
| `default` | 사용률 90% 이상 또는 여유 768MiB 미만 | 사용률 80% 미만·여유 768MiB 이상·쓰기 검사 성공 |
| `high-utilization` | 사용률 97% 이상 또는 여유 3GiB 미만 | 사용률 95% 미만·여유 4GiB 이상·쓰기 검사 성공 |

따라서 95%라는 값만으로 모든 큐가 중단됐다고 판단하면 안 됩니다. `high-utilization`에서 이미 `ready`이고 여유가 3GiB 이상이면 95%에서도 진행할 수 있습니다. 반대로 `capacity_limited`였던 큐는 95% 이상에서 재개하지 않습니다. 실행 환경의 `MARKET_STORAGE_PROFILE`과 해당 큐 상태를 함께 확인해야 합니다. 이 점검에서는 운영 프로필을 새로 조회하지 않았습니다.

최초 주식단위 근거만 별도로 허용하는 공간 예외는 없습니다. 정상 루프의 새 intent 경로는 공간 제한을 우회하지 않습니다. 다만 `prepare_bootstrap`, `service_xbrl_intent`, `collect_bootstrap_note`, `collect_events`를 직접 호출하면 루프의 검사가 자동 적용되지 않습니다. 수동 실행자는 같은 공간 검사부터 해야 합니다. `archive_only=True`는 네트워크 금지이며 DB 읽기 전용을 뜻하지 않습니다.

## 마지막 실제 관찰과 미완료 구분

2026-10-11 22:51 KST의 `../20261011-v132/INITIAL_SHARE_CANDIDATES.md` 관찰입니다.

| 대상 | 확인한 내용 | 다음 입력 |
|---|---|---|
| LG전자 066570 | 연속 TTM 입력 부족. 2026년 반기 원문 SHA `384e80262f96da1506ef4804b0eb7e1444c050c1babe8284a1c980ecc3cb0a77`에서도 2026Q2 CFS EPS 3,723원/주와 재계산 3,721.7398원/주 불일치 | 누락 연간·반기 입력과 EPS 주석의 귀속이익 배분 의미 확인. 차이 1.2602원/주가 기존 허용오차 0.5031원/주를 초과하므로 허용오차 확대 금지 |
| NAVER 035420 | 확인한 EPS 관련 사실에 검증된 가중평균 주식 수·보통주 귀속이익 부족 | EPS 주석 원문·계정 매핑. 지배기업 전체 이익으로 대체하지 않음 |
| 기아 000270 | 2025-07~2026-06 입력과 기존 주식기준 관찰 | 실제 차트 기준·기간 대조. 기존 기준을 최초 후보로 중복 승격하지 않음 |
| 현대차 005380 | 기존 주식기준 있음. 제한된 사실 조회가 OperationalError로 끝남 | 5초 제한·SQLSTATE를 보존한 회사별 재확인. 자료 없음으로 처리하지 않음 |

LG의 당시 작업 누락 연간·반기 접수번호는 `20260706000276`, `20260313000662`, `20250814002000`, `20250317001029`입니다. 새 경로는 원본 목록의 회사 연결까지 확인된 경우에만 이 유형을 큐에 넣을 수 있습니다. 이번에는 각 목록 원문 해시를 운영에서 재확인하지 않았으므로 실제 등록 가능 여부는 아직 미검증입니다. 분기 누락 `20260515000856`, `20251114001332`, `20250515001401`은 실제 재무 응답의 보고서 코드가 필요합니다.

배당 날짜의 필드별 상태와 보통주·우선주 구분은 구현돼 있지만 전 종목 지급일 확보를 뜻하지 않습니다. 최신 가격·전체 과거 자료의 현재 종목 수, 404 해결 수, 신규 장중 체결·호가 수신도 이번 읽기 점검에서는 측정하지 않았습니다.

## 읽기 전용 상태 조회안

현재 서비스의 기존 인증 환경을 사용하는 운영 담당자가 PostgreSQL에서 실행할 좁은 조회입니다. 비밀값을 명령행에 넣지 않습니다. `Store()`·`FactStore()` 생성자는 스키마·디렉터리를 만들 수 있으므로 읽기 점검에 사용하지 않습니다. 아래는 실행 예시이며 이번에 실행한 결과가 아닙니다.

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '500ms';
SELECT key, updated_at,
       payload::jsonb ->> 'status' AS status,
       payload::jsonb ->> 'profile' AS profile,
       payload::jsonb ->> 'used_percent' AS used_percent,
       payload::jsonb ->> 'cursor' AS cursor,
       payload::jsonb ->> 'next_code' AS next_code,
       payload::jsonb ->> 'next_at' AS next_at,
       payload::jsonb ->> 'retry_after' AS retry_after
FROM market_snapshots
WHERE key IN ('storage-capacity:share-basis', 'storage-capacity:xbrl',
              'storage-capacity:dated-accounts', 'storage-capacity:corporate-events',
              'toss-backfill-progress', 'dart-period-progress',
              'share-basis-bootstrap:066570', 'share-basis:066570',
              'share-basis-window:000270', 'corporate-events-job:066570',
              'corporate-event-index:066570', 'dart-provider-retry');
SELECT key, updated_at, payload::jsonb ->> 'status' AS status,
       payload::jsonb ->> 'raw_hash' AS raw_hash,
       payload::jsonb ->> 'report_code' AS report_code,
       payload::jsonb ->> 'publication' AS publication
FROM market_snapshots
WHERE key IN ('xbrl-job:20260706000276', 'xbrl-request:20260706000276',
              'xbrl-bootstrap-intent:20260706000276',
              'xbrl-job:20260313000662', 'xbrl-bootstrap-intent:20260313000662');
SELECT id, company_id, account_id, period_end, available_at, filing_id
FROM market_financial_facts
WHERE company_id = '00401731' AND period_end >= '2024-07-01'
ORDER BY period_end DESC, id
LIMIT 501;
ROLLBACK;
```

501건이면 잘림으로 처리하고 전체 확보를 선언하지 않습니다. 사실 원문은 반환된 ID에서 검토할 EPS 입력만 별도 선택합니다. `events:066570`, `dividend-observations:066570`도 지정 키의 기간·종류·날짜·검증 상태만 선택하며, 원본 응답·환경변수·토큰 전체를 출력하지 않습니다. 읽기 전용 조회가 시간 제한에 걸리면 SQLSTATE와 대상 키를 남기고 같은 큰 조회를 반복하지 않습니다.

## 공간 조건 충족 후의 제한 실행 후보

다음은 운영 담당자의 실행 후보이며 이번 작업에서 실행하지 않았습니다. 같은 큐의 정상 worker와 수동 실행을 동시에 돌리지 않고, 현재 작업 완료·체크포인트를 확인한 뒤 하나만 실행합니다. 재실행 전에 이전 호출 상태를 읽어 중복 실행을 막습니다.

1. **누락 입력 준비 1회사:** 현재 배포 환경에서 `collection_capacity(store, 'share-basis')`가 ready일 때만 `prepare_bootstrap(store, '066570')` 한 번. 최대 4개 요청·intent를 기록하며 새 원문 다운로드나 검증 승격은 하지 않습니다. 외부 실행 제한 45초·종료 유예 5초. 결과는 상태·접수번호·요청 수만 기록합니다.
2. **XBRL 1슬롯:** `collection_capacity(store, 'xbrl')`가 ready이고 인증·공급자 backoff가 허용할 때 `await service_xbrl_intent(source)` 한 번. 최대 1건이며 전역 대기열의 오래된 intent가 선택될 수 있으므로 LG만 처리한다고 보고하지 않습니다. 외부 제한 60초. 실패 시 공급자 대기시간과 원문을 보존하고 즉시 반복하지 않습니다.
3. **기존 배당 원문 재해석 1회사:** `collection_capacity(store, 'corporate-events')`가 ready일 때 `await collect_events(source, '066570', archive_only=True)` 한 번. 외부 제한 45초. 원문 부족 시 새 다운로드로 전환하지 않습니다. 이 호출은 기존 문서의 해석·배당 관찰 연결을 DB에 쓸 수 있습니다. 지급일·권리일의 검증 승격은 확보된 개별 근거에 한정합니다.
4. **정상 전체 과거 수집 재개:** 위 조건과 운영 프로필을 확인한 뒤 기존 `TossBackfill.step()` 1회 또는 정상 단일 worker를 제한 시간 동안 관찰합니다. `--once` worker 전체 실행은 사용하지 않습니다. 최신/과거 커서, 공급자 429·인증 상태, 디스크 증가를 전후 비교합니다. 원천 마지막 페이지 전에는 완료로 바꾸지 않습니다.

단독 함수 호출에는 실행 담당자가 공간 검사·외부 제한·단일 실행 조건을 묶는 래퍼가 필요합니다. 검증 없는 새 CLI가 이미 존재하는 것처럼 제시하지 않습니다. `scripts/verify_dart_ttm_inputs.py`는 여러 보고서를 호출하고 월 기반 분기 추정이 남아 있어 이번 제한 실행 도구로 사용하지 않습니다. 전체 아카이브 재생 스크립트도 1회사 제한이 없어 제외합니다.

최초 주식단위의 명시 검토·promotion은 위 수집 작업과 별개입니다. LG 불일치나 NAVER 입력 공백이 남은 채 큐 상태만 바꾸지 않습니다. 원본·정정·정상 데이터는 보존하며 이번 기록은 전 종목 연결 완료나 공개 배포 완료를 선언하지 않습니다.
