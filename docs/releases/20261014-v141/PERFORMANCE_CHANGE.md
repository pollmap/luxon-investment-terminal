# 검색 잠금·관측 버전 수정

2026-10-14 KST. 기준 제품 소스는 `6e7b6ce`(v140)입니다. 이 문서는 코드와 오프라인 회귀 검사 기록입니다. v141 공개 성능 통과 기록은 아닙니다.

## 공개 측정에서 확인한 회귀

Windows PC의 공개 인터넷에서 HTTP/2 독립 TLS 연결 100개, 연결당 동시 요청 1개로 측정했습니다. 연결 수립은 요청 시간과 분리했고, 본문 수신 종료까지 기록했습니다. `warm`은 후속 3배치이며 캐시 적중을 보장하는 명칭이 아닙니다.

| 검색 p95 | v139 | v140 |
|---|---:|---:|
| 첫 배치, 본문 수신 종료 | 9,576.48ms | 7,454.30ms |
| 후속 3배치, 본문 수신 종료 | 1,830.33ms | 4,215.21ms |
| 후속 배치의 현재 app 표본 | 1,482.70ms | 3,426.96ms |
| 1,600ms 대기 후, 본문 수신 종료 | 1,073.79ms | 2,285.83ms |
| 1,600ms 대기 후 현재 app 표본 | 750.00ms | 1,954.46ms |

v140 후속 배치 300건은 origin `EXPIRED` 158건·`HIT` 142건입니다. `HIT`의 과거 Server-Timing은 현재 app 통계에서 제외했습니다. v139 후속 배치는 `EXPIRED` 290건·`MISS` 10건입니다. 두 기록 모두 오류 0건이지만 검색 목표 300ms는 미달이며, v140 검색 회귀를 성공으로 취급하지 않습니다. 상세 요약의 후속 p95 약 1,483→600ms 개선은 보존합니다.

원본 기록:

- `work/finish-v140/performance-before-v139.json`: 2026-10-13 14:08:33.221 UTC, SHA-256 `913cde2751d9a4fd7ab77ae4ca5170683249aa1a7c411e2b423d0fde2e546fcd`.
- `work/finish-v140/performance-after-v140.json`: 2026-10-13 15:11:05.630 UTC, SHA-256 `9d15222ec2393a2f8a99f17188046ed1de03155d779ca72e2be3ac94ee9805ac`.
- 공개 집계: [v140 측정 기록](../20261013-v140/performance-after-v140.json), [v139 기준선](../20261013-v140/performance-before-v139.json).

### 실제 EXPIRED 표본

아래는 `timing_is_current=true`, edge `ORIGIN`, origin `EXPIRED`인 실제 표본입니다. 앱의 `response_hit`는 Nginx HIT와 다른, 버전 검사를 통과한 서버 본문 재사용입니다. 중첩 stage 시간을 합하거나 서로 다른 p95를 빼서 네트워크 시간을 만들지 않습니다.

| 표본 | 네이버, 만료 배치 | LG, 후속 배치 |
|---|---:|---:|
| 서버 Date(UTC) | 10-13 15:11:26 | 10-13 15:11:19 |
| 본문 수신 종료 | 2,278.565ms | 4,168.423ms |
| app | 1,958.91ms | 3,379.53ms |
| revision_wait | 81.35ms | 8.00ms |
| search_lock | **1,661.33ms** | 0.00ms |
| response_lock | 0.00ms | 0.00ms |
| flight_wait | 1,835.96ms | 3,360.69ms |
| response_hit / flight_wait 횟수 | 1 / 1 | 1 / 1 |
| search_lock 횟수 | 2 | 2 |
| revision_wait / revision_join 횟수 | 1 / 1 | 1 / 없음 |
| executor_wait 횟수 | 1 | 2 |

네이버 표본은 본문을 재사용해도 식별 캐시 잠금에 약 1.66초를 기다렸음을 직접 보여줍니다. LG 표본은 이 잠금만으로 전체 지연을 설명할 수 없음을 보여줍니다. 다른 EXPIRED LG 표본에는 app 3,510.55ms 대비 revision_wait 111.64ms·flight_wait 109.53ms의 미계측 구간도 있습니다. 이벤트 루프·ASGI 대기 등의 구체적인 원인은 이 기록만으로 확정하지 않습니다. 클라이언트 벽시계와 서버의 약 1초 차이가 기록돼 있으며, 해당 freshness 표시를 가격 최신성 통과 근거로 사용하지 않습니다.

## 좁은 수정

- `search_view.py`: 전체 조회·차분 SQL·디코딩을 공용 캐시 잠금 밖으로 옮겼습니다. 원천별 build 잠금은 동일 원천의 중복 재구성을 합치고, 짧은 캐시 잠금은 조회·LRU·완성된 세대 교체만 보호합니다. 따라서 이미 관측된 정확한 세대의 조회는 다른 원천 SQL에 막히지 않습니다. 동기 SQL은 기존 worker에서 실행하며, 새 비동기 DB 드라이버를 도입하지 않았습니다.
- PostgreSQL 전체 projection의 `entries`를 `MATERIALIZED`로 바꿨습니다. 기존 legacy·종목별 우선순위·빈 sentinel·원천 head·출력 필드는 유지합니다. 반복 JSONB 변환을 줄일 수 있다는 코드상 가설이며, 실제 실행계획과 운영 효과는 **미측정**입니다.
- `app.py`의 `/search`: 이름·별칭 일치에 실제 사용한 projection의 heads를 함께 보존합니다. 이전에는 일치 판단 후 다른 worker가 공용 캐시를 교체하면 `candidate_dependencies`가 최신 캐시 heads를 다시 읽어, 이전 값과 새 버전을 결합할 수 있었습니다. 이제 같은 불변 세대의 값·heads를 사용하고 최종 관측이 달라지면 본문을 새 버전으로 캐시하지 않습니다.

가격 TTL·유효 기간, summary, 응답 필드, 빈 검색 정렬, 별칭·초성·우선주, 후보 최대 512개 및 반환 최대 30개 의미는 변경하지 않았습니다. quote delta의 조회 전후 heads 검사, legacy·삭제·동시 writer fallback을 유지합니다.

## 검증과 남은 판정

선택한 비운영 SQLite fixture 52개가 **3.69초에 통과**했습니다. F lint와 변경 범위 diff 검사를 통과했습니다. assertion을 완화하지 않았습니다.

- 다른 원천 SQL을 명시적으로 멈춘 상태에서 정확한 cached 세대가 응답하는 병렬 검사.
- 일치 판단 후 공용 cache 교체가 있어도 이전 값의 heads를 유지하는 검사.
- 실제 route에서 그 교체를 재현해 첫 본문은 새 heads로 캐시되지 않고, 다음 정상 요청만 캐시되는 검사.
- quotes/enriched PostgreSQL projection의 materialization·원본 열·바인딩 컴파일 검사 2개.
- 기존 응답 동일성, 80번째 후보의 거래대금 순위 승격, 다른 회사 갱신 격리, 별칭 추가 발견, 최종 writer 검사, 100요청의 공유 버전 조회 기준.

검사 파일은 `tests/test_search_selected_projection.py`, `tests/test_search_revision_reuse.py`, `tests/test_market_revision_batch.py`입니다. PostgreSQL 실행·운영 DB 변경·공급자 호출·공개 부하·배포는 이 담당 범위에서 수행하지 않았습니다.

차가운 조회의 response stripe 충돌과 미계측 app/flight 지연은 남아 있습니다. 이번 수정이 공개 p95를 통과한다고 추정하지 않습니다. root가 통합 CI와 배포 후 동일 조건의 **한 번의 공개 후속 측정**으로 판정할 예정입니다. 같은 원인의 수정·재측정 상한을 유지하며, 결과가 미달이면 원인과 영향만 기록하고 반복 시험하지 않습니다.
