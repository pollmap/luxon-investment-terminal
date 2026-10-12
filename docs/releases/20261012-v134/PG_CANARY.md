# PostgreSQL 17 읽기 전용 후보 검사

2026-10-12 23:54 KST. **현재 운영 PostgreSQL 17.11에서 후보 검색 SQL의 검사 10개가 모두 통과했습니다.** 합성 `VALUES` 입력을 사용한 SQL 호환·분기 검사이며, 운영 금융 자료의 정확성이나 공개 성능 통과를 의미하지 않습니다.

## 실행 범위

- 기존 SSH 설정과 `korea-stocks-market-api-1`의 Python 환경 사용.
- 관측 release: `20261012-incremental-evidence-v133`.
- API: 실행 중, image `sha256:bb2a462b5b0c394a178f5a5b7f313a35b786b7d077da2a39ea9d39646a7f4bcd`.
- 후보와 검사 파일을 호스트·컨테이너 `/tmp`에만 복사. `/app`의 운영 코드는 교체하지 않음.
- DB 트랜잭션 `READ ONLY`, 각 SQL `statement_timeout=5s`, 마지막에 rollback.
- `market_snapshots`는 합성 `VALUES` CTE로 가림. 운영 테이블 읽기 0건, DDL·DML·임시 테이블 없음.
- API·작업자·DB·Redis·다른 서비스 재시작 없음. 공급자 연결·API 요청·부하 시험 없음.

```text
docker exec -e PYTHONPATH=/app korea-stocks-market-api-1 python /tmp/pg_readonly_canary.py --candidate-search-view /tmp/search_view.py --candidate-request-timings /tmp/request_timings.py
```

## 후보와 검사 식별

| 파일 | SHA-256 |
|---|---|
| `services/market_dashboard/search_view.py` | `b52c9f3831a8728fc22d6083ddb45de1d2eca17b6d5071a0c212278b7e30f182` |
| `services/market_dashboard/request_timings.py` | `f9eaae19ae6dc82598ff80f682901a2a9aaa0fdb27a16e15d92454525f9d6469` |
| `work/operations-v133/pg_readonly_canary.py` | `e55c46d3a47938225e747ee48e8692b5c76d739a75c8b9682f450641cfc7d7c3` |

실행 입력은 별도 로컬 사본으로 고정했습니다. 컨테이너가 계산한 두 후보 SHA와 사본 SHA가 일치하며, 종료 후 프로젝트 후보가 사본과 같은지도 확인했습니다.

## 정확한 결과

| 검사 | 결과 |
|---|---|
| `cold_full_projection` | 통과 |
| `materialized_changed_row` | 통과 |
| `interleaved_write_fallback` | 통과 |
| `deleted_incremental_restores_legacy` | 통과 |
| `legacy_replacement_fallback` | 통과 |
| `bounded_large_change_fallback` | 통과 |
| `selected_values_cte_and_projection` | 통과 |
| `compact_selected_projection` | 통과 |
| `stale_projection_fallback` | 통과 |
| `empty_full_sentinel` | 통과 |

종료 코드 0, `passed=true`, `stage=complete`, `server_version_num=170011`, 후보 SQL 18회, 전체 projection 6회, materialized delta 2회입니다. 합성 입력은 `fixture_non_production`으로 표시했습니다.

`compact=True` 검사는 필요한 필드만 반환하면서 명시적인 `null`, 중첩 `source_trace`의 boolean·null·한글, 필요한 `facts` 하위 항목을 보존하고 불필요한 이력을 제외하는지 확인했습니다. PostgreSQL JSON 경로의 `ARRAY(Text)` 변환도 실제 서버에서 통과했습니다.

실행 receipt와 전체 결과는 `work/operations-v134/pg-canary/receipt.json`, `result.json`에 보존했습니다. 제품 코드 변경·배포와 공개 100동시 요청 성능은 이 검사와 별도로 판정해야 합니다.
