# v130 근거 검색 스키마 조회 통합

범위: `research_search.py`와 `test_research_search.py`만 수정했습니다. 공개 요청·SSH·PostgreSQL 실행·배포·원천 호출은 하지 않았습니다.

## 관측과 변경

v129 공개 기록은 연구검색 20요청 중 19개 200·1개 429, 성공 p95 약1,880ms입니다. DNS/TLS와 인터넷 경로를 포함하므로 이 수치를 DB 처리시간으로 해석하지 않습니다.

코드상 매 요청 `inspect(engine)`와 여러 `has_table`이 별도 연결을 사용하며, 이후 PG 확장·vector 열 확인을 추가로 실행했습니다. 새 `catalogue_state`는 PostgreSQL에서 테이블4개·확장·열 확인을 한 SELECT로 묶고, 본문 검색과 동일한 `read_db` 연결 및 문장 timeout 안에서 읽습니다. SQLite는 기존 reflection을 같은 연결에서 실행합니다.

스키마 결과와 응답을 TTL로 보관하지 않습니다. SearchBudget의 rate120/active4/queue20/wait0.75초, 기존 latest revision 정렬·count·본문/제목 검색·RRF·출처 응답은 바꾸지 않았습니다. 정확한 전역 변경 토큰 없는 응답 병합은 도입하지 않았습니다.

## 로컬 검증

- 관련57개+신규2개 = **59개 통과**, 실패/오류/건너뜀0. `work/research-search-v130-tests.xml`.
- Ruff 통과.
- PostgreSQL metadata SELECT의 파라미터·1회 실행·캐시 없는 capability 변경을 순수 mock으로 검증했습니다. 실제 PG 실행 검증은 아닙니다.
- SQLite 실제 읽기 연결1회, 이후 색인 생성·문서 삭제 즉시 반영, GET 비쓰기와 이전 회귀를 통과했습니다.
- Windows WMI 오류 때문에 검증 실행기에서 `platform.machine`만 AMD64로 고정했습니다. JUnit hostname 생성에서 WMI 경고가 추가됐지만 exit0 및 XML59/0/0을 확인했습니다. 앱 코드는 이 우회를 포함하지 않습니다.

`compare_catalogue.py`는 실제 v129 배포 tar의 파일만 별도 경로에 추출하여 같은 SQLite 자료·같은5검색어×4번과 비교했습니다. 각 응답은 elapsed_ms를 제외하고 전체 동일했습니다.

| 로컬20회 | v129 연결 checkout | 후보 checkout | SQLite SQL 수 |
|---|---:|---:|---:|
| 기존 제목만 | 100 | 20 | 양쪽140 |
| 청크 색인 있음 | 120 | 20 | 양쪽160 |

SQLite SQL 수는 그대로이며 PostgreSQL 왕복 절감은 코드와 바인딩 검사에서 확인한 범위입니다. 원점 또는 공개 p95 개선·429 해소를 주장하지 않습니다. 공개 성능은 루트가 v130에서 별도 측정합니다.

## 동결

- baseline research_search.py: `b4df3adb9c7582c4adf6c25e921c88c1a8116e54ab81bee7f6499a912536debd`
- candidate research_search.py: `a8cc4ec285f73daa20cc3e48a271c8583e38680261a8928cbf484934f0321d2b`
- test_research_search.py: `8393fc681989e388b460a77b6f70bd54ce748f33858b4be3e87c466e0690a982`

기준 tar는 `work/releases/20261011-candle-research-v129/backend-patch.tar.gz`이며 원본을 변경하지 않았습니다. 비교 자료는 `comparison.json`, 재현 도구는 `compare_catalogue.py`에 있습니다.
