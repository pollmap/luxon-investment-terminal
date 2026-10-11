# v133 검색 갱신 범위와 요청 단계 계측

2026-10-12 KST. v132의 두 공개 시험은 검색 300ms·요약 500ms 목표 미달이었다. 이번 작업은 해당 결과를 읽고 수행한 코드 수정과 fixture 검증이며, 새 공개 부하 시험이나 개선율 측정이 아니다.

## 변경

- 검색의 시세 맵은 기존처럼 최신 저장 버전을 검사한다. 버전이 바뀌었을 때 원본 호환 snapshot과 각 종목의 작은 revision 열부터 비교한다.
- 기존 호환 snapshot이 같고 변경된 종목이 30개 이하면 해당 종목만 좁은 필드로 조회한다. 조회 전후 모든 시세 revision이 같을 때만 기존 맵과 합쳐 재사용한다.
- 호환 snapshot 변경, 삭제, 30종목 초과 변경, 조회 중 쓰기 감지 시 기존 전체 투영으로 한 번 복귀한다. 이를 무한 재시도하지 않는다.
- 최초 전체 시장 검색은 전체 경량 맵을 유지한다. 검색 결과 최대 30종목 hydration, 거래일 기준 거래대금 정렬, 별칭·초성, 기존 응답 필드를 유지한다. 가격 TTL·캐시 유효시간·DB 스키마·writer는 바꾸지 않았다.

## 계측의 뜻

| 이름 | 측정 경계 | 해석 제한 |
|---|---|---|
| read_guard | 메모리 SQLite 공유 연결 잠금 획득 | PostgreSQL 풀 대기가 아님 |
| db_checkout | SQLAlchemy engine.connect | 풀 획득, 최초 연결·pre-ping 포함. 순수 pool queue로 단정하지 않음 |
| revision_sql | revision SQL 실행 호출 | SQL 구성·드라이버·DB 왕복이 포함될 수 있음 |
| revision_result | revision 결과 가져오기·dict 변환 | 순수 PostgreSQL CPU 시간이 아님 |
| search_lock | 검색 맵 공유 잠금 획득 | 잠금 안 조회·변환과 분리 |
| search_sql / search_result | 검색용 SQL 호출 / 결과 수신 | 앞의 전체 revision 시간과 같은 범위가 아님 |
| search_decode | 좁은 검색 맵·선택 회사의 Python 값 변환 | SQL 드라이버에서 이미 수행한 변환은 포함하지 않음 |
| response_json / response_gzip | JSON 직렬화 / gzip 압축 | 기존 response_encode의 하위 구간 |

기존 revision_db·response_encode 등의 합계는 유지했다. 하위 구간과 합계를 더하지 않는다. 연결 반환·트랜잭션 종료 등은 하위 구간에 모두 포함되지 않는다. 공유 작업은 실행한 요청에만 기록하고 대기자는 대기 시간만 기록한다. 값·종목코드·SQL·URL·인증값을 헤더에 넣지 않는다.

search_full·search_delta·search_delta_rows·search_fallback 카운터는 전체 조회·증분 재사용·변경 행 수·전체 복귀를 구분한다. 정적 캐시 HIT의 이전 헤더를 현재 요청의 DB 시간으로 해석하지 않는다.

## 검증

- 검색 투영·요청 계측·100개 fixture 요청의 revision 병합, 3파일 총 41개 통과. JUnit 기준 16.358초. 공개 인터넷 시험이 아니다.
- 시세 한 행 갱신에서 전체 json_each가 실행되지 않고 해당 한 행만 투영되는지 확인했다. 기존 전체 계산과 검색 결과·정렬·가격·별칭이 일치했다.
- 이전 호환 snapshot 쓰기, 행 삭제, 31행 변경, 선택 조회 중 다른 종목 쓰기에서 전체 조회로 복귀하고 잘못된 revision으로 캐시하지 않는지 확인했다.
- 고정 시계와 분리된 연결/SQL/결과 fixture로 계측 경계를 검사했다. 응답 바이트·ETag·캐시 헤더와 다음 시세 갱신 검사는 유지했다.
- 첫 검사에서 기존 검사 36개는 통과했고 새 fixture 5개의 naive/aware 시각 및 legacy 쓰기 가정을 수정했다. 실제 시간 검증 로직은 완화하지 않았다.
- 재검사는 Windows pytest의 조상 경로 samefile/lstat 수집 단계에서 180초 제한으로 종료돼, 원본과 SHA가 같은 세 테스트 파일만 작은 staging 경로에 복사해 실행했다. 제품 소스는 현재 저장소를 그대로 import했다. 테스트 내용·CI 네트워크 정책을 바꾸지 않았다.
- 변경 5파일의 전체 Ruff와 store.py의 오류 중심 Ruff 통과. store.py의 기존 전체 스타일 경고를 해결했다고 주장하지 않는다.
- 최종 선택 회사 JSON 변환 계측 후 실제 검색/요약 응답과 한 행 갱신 사례 2개를 다시 통과했다. 앞의 41개에 포함된 사례로 고유 검사 수에 더하지 않는다.

근거: work/operations-v133/search-incremental-timings.xml, search-final-timing-smoke.xml, perf-test-stage-manifest.json. 실제 PostgreSQL 실행계획·공개 성능·공개 배포는 root 통합 검증 전이다. 풀 크기·타임아웃을 임의로 늘리거나 v132와 같은 조건의 부하 시험을 반복하지 않았다.
