# 검색·상세 요약 조회 변경

2026-10-13. 기준 소스 `d923179c7e32231548414b15b1501ca92baa7531`과 작업 트리 비교입니다. 이 기록은 구현·오프라인 검증이며 공개 배포 성능 통과 기록이 아닙니다.

## 원인과 변경

| 코드에서 확인한 비용 | 변경 |
|---|---|
| 식별용 quote delta도 31개 변경부터 전체 시세 projection으로 돌아감 | 식별 필드는 512개까지 변경된 종목만 읽음. 기존 순위 전체 projection의 30개 기준은 보존 |
| 31개 이상 이름 일치가 전체 시장 순위 준비로 돌아감 | 31~512개 후보의 최소 순위 필드만 읽고, 실제 반환할 최대 30개에만 화면 필드 결합 |
| 좁은 검색 응답이 전역 enriched 버전에 의존함 | 해당 후보의 `enriched-item`과 quote 버전 사용. 새 별칭·추가 일치는 전체 식별 검사로 발견하며 legacy writer의 버전 검증 유지 |
| compact 조회가 요청마다 많은 SQLAlchemy JSON 표현식·VALUES를 새로 구성함 | 내부 고정 경로와 바인딩한 코드 배열로 SQL 템플릿 재사용. 선택 projection을 MATERIALIZED로 한 번 평가 |
| summary가 가격 변경마다 전체 회사 재무·프로필 JSON을 읽고 회사 전체를 서명함 | 한 종목의 화면 필드·필요 facts·회사 products/sector/listed_at만 읽는 `summary_row` 분리 |

`/api/market/v1` 응답 필드, 빈 검색 정렬, 별칭·초성·우선주·대상 필터, 정상 가격·출처를 보존합니다. 빈 검색과 512개 초과 일치는 전체 시장 순위 의미를 유지합니다. 순위·조건검색·full 상세 경로는 변경하지 않습니다. TTL과 가격 유효 기간을 연장하지 않았습니다.

## 검증

`test_search_selected_projection.py` **34개 통과(12.08초)**, 변경 범위 F lint·diff 검사 통과입니다.

- 기존 목록과 검색 결과·순서·total·source_trace 동일성.
- 초기 limit 밖 80번째 종목의 거래대금 순위 승격.
- 80개 일치의 순위 입력 80개와 반환 hydration 1~30개의 분리.
- 이름·코드·초성·별칭, preferred/excluded 경계, legacy·삭제·동시 버전 변경 fallback.
- 다른 회사의 quote·enrichment 변경 시 응답 재사용, 실제 선택 회사 변경 시 갱신.
- summary의 전체 회사/quote reader 호출 차단, 이전 요약과 응답 동일성, 주가 변경 반영.
- PostgreSQL SQL은 typed text[] 코드 바인딩·MATERIALIZED·내부 고정 JSON 경로를 컴파일 검사했습니다. 실제 PostgreSQL 실행계획·운영 잠금·전송 지연은 측정하지 않았습니다.

## 작은 모의 계측

실행: `.venv/Scripts/python.exe tests/bench_search_candidates.py work/lucian-v140/search-projection-microbench-verified.json`

조건: 별도 SQLite 메모리 DB, 비운영 280종목·넓은 검색 80일치, 질의당 4회, 매번 40개 시세 변경, 단일 순차 실행입니다. 원천 시각·출처를 갖춘 변경이며 매번 실제 quote revision 변경을 assertion으로 확인합니다. 공급자·운영 DB·공개 인터넷 호출은 0회입니다. 검색 projection만 측정하며 100동시 HTTP·전체 app·캐시 적중·네트워크 성능 측정이 아닙니다.

| 질의 | 이전 중앙값 | 변경 후 중앙값 | 이전→변경 후 decode 행 수/회 |
|---|---:|---:|---:|
| 삼성 | 31.833ms | 12.643ms | 586→224 |
| 별칭79 | 26.096ms | 5.181ms | 283→42 |
| 빈 검색 | 41.291ms | 11.258ms | 305→305 |

빈 검색의 전체 순위 입력은 줄이지 않았습니다. 그 구간 시간 감소는 compact SQL 준비·평가 비용 감소와 실행 환경 변동을 포함합니다. 4회의 작은 모의 중앙값을 실제 서비스 p95나 서비스 전체 배수 개선으로 환산하지 않습니다.

초기 prototype은 넓은 검색 중앙값이 19.477→32.166ms로 늘어 고정 SQL을 추가하는 근거가 됐습니다. 당시 단순 price patch는 출처·시각 보존 규칙 때문에 거부돼 revision 만료 부하를 재현하지 못했습니다. 초기 두 계측 파일은 폐기할 운영 원본이 아닌 진단 기록이며, 완료 근거는 위 `verified` 파일만 사용합니다.

## 남은 확인

root가 같은 공개 버전·질의·종목·연결 조건에서 운영 PostgreSQL 및 공개 100연결 후속 시험을 한 번 수행해야 합니다. 검색 p95 300ms·상세 요약 500ms 목표는 아직 이 수정으로 통과했다고 판정하지 않습니다. HTTP 연결 수립·인터넷 왕복·서버 실행·캐시 상태를 분리해서 비교해야 합니다.

광범위한 512개 초과 검색, legacy 전체 source의 식별 projection 재구성, 수집과 조회의 DB 경쟁은 남은 경로입니다. PostgreSQL 실행계획 없이 그 비용이 실제 주원인이라고 단정하지 않습니다. 별도 캐시 나이 증가·데이터 유효 시간 연장·DB 변경·증설·공개 부하시험·배포는 이 작업에서 수행하지 않았습니다.

## 통합 CI의 버전 조회 회귀 수정

최초 통합 CI는 2개 실패·2,894개 통과였습니다. 종목별 enrichment 격리를 위해 `candidate_dependencies`에서 동기 `read_revisions`를 추가한 탓에, 100요청/10검색어의 버전 조회가 기존 공유 1회에서 11회로 늘었습니다. 최초 응답의 관측·최종 검증도 2회 대신 4회가 됐습니다. 기존 assertions는 완화하지 않았습니다.

추가 버전 조회를 제거했습니다. 이미 수행하는 enrichment 식별 projection SQL이 각 회사의 item head와 projection generation 일치 여부를 동일한 DB 스냅샷에서 함께 읽습니다. 검색 dependency는 이 관측값을 재사용합니다. 정상 projection의 없는 회사는 missing marker를, legacy/불일치 projection은 관측된 전역 source 버전을 사용합니다. 다른 회사의 정규 갱신은 격리하며, 새 별칭·후보 변경 발견과 동시 writer의 최종 검사는 유지합니다.

`test_market_revision_batch.py`, `test_search_revision_reuse.py`, `test_search_selected_projection.py` **47개 통과(13.02초)**입니다. 100요청/10키의 공유 버전 조회 1회, 최초 응답 2회·그다음 cache hit까지 누적 3회라는 기존 기준을 그대로 통과했습니다. F lint·diff 검사도 통과했습니다. 이 후속 검증은 로컬 비운영 SQLite이며, 실제 복원·운영 DB·공개 부하를 추가 실행하지 않았습니다.
