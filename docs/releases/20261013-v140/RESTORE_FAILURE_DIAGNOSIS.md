# 독립 복원 중단 원인 조사

2026-10-14 KST. 읽기 전용 조사이며 운영·원본·복원 DB를 변경하지 않았습니다. 새 PostgreSQL 실행, DB 질의, 전체 해시 재실행, 삭제 및 복원 재시도를 하지 않았습니다.

**확정된 실패는 전역 여유 공간 보호 기준에 의한 중단입니다. 공간을 사용한 파일·프로세스의 원인은 아직 미확정입니다. 최적화된 재생이 빨라졌어도 복원은 통과하지 않았습니다.** 같은 공간 실패가 반복됐으므로 다음 전체 실행보다 별도 원인 계측과 실행 계획의 변경이 먼저입니다.

## 1. 실제 실행 증거

대상: `work/target-restore/82cf96ad6b684cfab19678431ad8f171/resume-1e2e43d093744ffca15fee7687aaef5e`.

| 항목 | 확인한 값과 의미 |
|---|---|
| 실제 CLI 종료 | `work/finish-v140/restore-exit.txt`: **1**. 부모 도구의 종료와 구분합니다. |
| RESULT | `state=interrupted`, `phase=prefix_restore`, `restore_verified=false`, `data_equal=false` |
| 데이터 stream 종료 | `pipe.exit_code=1`, `exit_waited=false`, `stdout_eof_consumed=false`, `threads_stopped=true`. 제어용 pg_restore의 exit 0을 stream 정상 완주로 사용하지 않습니다. |
| 종료 시점 | PostgreSQL 로그: **10월 14일 00:00:29 KST**, 정상 fast shutdown 이후 database shut down |
| 소요 시간 | RESULT **2,514.265초**. 4시간 한도를 소진한 실패가 아닙니다. |
| 기존 DB의 정확한 count | **15,282,400행·8,071개 참조**, `matched_checkpoint`. 전체 값·해시 검증을 뜻하지 않습니다. |
| 이번에 검증한 재생 prefix | **4,226,800행**, 성공 10,567 batches, 다음 batch 중단. 새로 저장한 행 수가 아닙니다. |
| 실제 처리 중인 다음 batch | 4,227,200행까지 `processed`; `checkpoint_reload_required=true`. 이 수를 검증 완료로 세지 않습니다. |
| 최종 전체 검증 | 전체 **17,014,175행**, ordered hash, 전체 source reference 검증 및 stream 완주 **미통과** |
| 원본·운영 보존 | `archive_content_verified=true`, `input_paths_modified=false`, `production_contacted=false`, `cluster_stopped=true` |

고정된 원본 SHA는 `6c1861479624e5dfac87bdeb767579a818933ac87836b754490fe27cfcdf1c4c`, codec SHA는 `10be8d308dd6d0d73a123e8b125d366d3f420cbb23bd424c6a2bae57ea295198`입니다. 이번 조사는 저장된 manifest/RESULT 값을 읽었으며 원본 전체 해시를 다시 계산하지 않았습니다.

### 공간 수치

| 경계 | 바이트 |
|---|---:|
| 실행 시작 C: 여유 | 11,312,996,352 |
| 실패 당시 C: 여유 | 4,296,216,576 |
| 4GiB 최소 여유 + 다음 batch 예약 요구 | 4,297,352,480 |
| 실행 중 C: 여유 감소 | 7,016,779,776 |
| 클러스터 논리 크기: 시작 → 마지막 관측 | 9,400,163,921 → 9,400,109,823 |
| 논리 크기 차이 | **−54,098** |
| 종료 후 00:12:11 KST C: 여유 | 14,051,143,680 |
| 실패 당시 대비 종료 후 여유 회복 | **9,754,927,104** |

`stopped-cluster-allocation.json`은 종료 후 1,315개 파일의 논리 크기 9,399,579,381바이트와 파일 데이터 할당량 3,587,425,013바이트를 구분했습니다. 압축 파일은 11개, sparse 파일은 0개였습니다. 이는 **종료 후 경로에 남은 파일**의 계측이며 실행 중 삭제됐으나 열린 파일의 할당량은 측정하지 못합니다. 여유 회복은 00:12:11 관측과의 차이이므로 모든 바이트가 PostgreSQL 종료 순간 해제됐다고 단정하지 않습니다.

`resume_restore.py:37`의 cluster inventory는 경로에 존재하는 파일의 `st_size`를 더합니다. `:142`의 전역 free guard와 `:171`의 클러스터 성장 guard는 별개입니다. 이번 거부는 **전역 free < 4GiB + incoming**이며 +6GiB 자체 클러스터 성장 초과가 아닙니다. 최소 여유 및 4시간/+6GiB/16GiB 한도를 올려 해결하지 않습니다.

## 2. 가능한 원인과 반대 근거

### A. 열린 삭제 파일·Windows delete-pending: 가능한 현상, 이번 원인 미확정

PG17 공식 Windows `dirmod.c`에는 이미 unlink됐지만 다른 주체가 열고 있는 `STATUS_DELETE_PENDING` 처리와 공유 플래그 문제가 명시돼 있습니다. `md.c`도 비임시 파일에서 다른 backend의 열린 FD가 공간을 붙잡지 않도록 unlink 전 truncate하는 경로를 설명합니다. 따라서 **경로 기반 논리 크기는 일정하지만 열린 삭제 파일의 할당 공간이 프로세스 종료 시 해제되는 현상**은 진단 후보가 맞습니다. [PG17 Windows unlink 구현](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/port/dirmod.c), [PG17 relation storage 구현](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/backend/storage/smgr/md.c)

그러나 이번 최적화의 `PrefixStage`는 첫 batch에서 테이블을 생성하고 계속 재사용합니다(`optimized_prefix_replay.py:158`). 각 COMMIT의 `ON COMMIT DELETE ROWS`는 PG17 `heap.c`에서 **기존 relation 및 TOAST의 즉시 in-place truncate** 경로를 사용합니다. `mdtruncate`는 segment를 0으로 줄여 유지하거나 닫고, 매 batch마다 새 relation 파일을 만들고 unlink하는 구현이 아닙니다. 그러므로 **10,567회 COMMIT마다 DROP/recreate한 삭제 파일이 7GB 쌓였다는 설명은 이 경로와 맞지 않습니다.** trace replay·append에 남은 기존 stage 경로와 Windows/NTFS 실제 할당은 별도 계측 대상입니다. [PG17 ON COMMIT](https://www.postgresql.org/docs/17/sql-createtable.html), [PG17 heap truncate 구현](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/backend/catalog/heap.c)

공식 `REL_17_STABLE` 소스 검토는 실제 배포된 17.11 바이너리와 모든 파일을 대조한 검증은 아닙니다. Windows 백신·파일 필터·매핑 핸들 및 NTFS 압축의 실제 영향도 측정하지 않았습니다.

### B. PG 실행 임시 파일·정렬: 제한이 있으나 명시적 TEMP table은 별도

실행 설정은 `work_mem=16MB`, `temp_file_limit=256MB`입니다(`resume_restore.py:550`). PG의 `temp_file_limit`은 query sort/hash/held cursor 등의 실행 임시 파일을 제한하지만 **명시적 임시 테이블의 디스크 사용은 포함하지 않습니다**. 따라서 이 옵션만으로 모든 stage 공간을 제한했다고 주장할 수 없습니다. [PG17 메모리·임시 파일 제한](https://www.postgresql.org/docs/17/runtime-config-resource.html)

각 prefix batch는 원문 400행/16MiB 제한을 통과하고 stage 및 PK 조회 결과도 해당 batch에 제한됩니다. 실제 7GB 증가를 한 번의 결과 집합이나 알려진 sort spill로 설명하는 증거는 없습니다. postgres.log에는 조사한 실행 구간의 WARNING/ERROR/PANIC 또는 임시 파일 크기 기록이 없습니다. `log_temp_files` 계측이 없는 상태이므로 로그가 조용하다는 이유로 임시 파일 자체를 배제하지 않습니다.

### C. Python stdout spool·trace cache: 현재 코드로 무제한 축적은 확인되지 않음

실제 pg_restore 데이터는 `restore_supervisor.py:222`의 PIPE와 **64KiB × 최대 4개 queue**로 전달됩니다. `stdout_bytes=5,250,183,227`은 누적 전송량이며 이를 5.25GB RAM·임시 파일이라고 해석하지 않습니다. stderr는 즉시 버립니다(`:296`).

`local_restore.py:330`의 `TemporaryFile`은 pg_ctl 등 제어 명령용입니다. stdout 4MiB·stderr 1MiB 한도와 ExitStack 정리가 있고, 실제 COPY stdout을 그 파일에 적재하지 않습니다. 자식 PG가 Windows에서 제어 출력 핸들을 유지할 가능성은 별도 확인 대상이지만 제어 로그 경로에서 7GB 증가한 증거는 없습니다.

새 trace cache는 256개/16MiB retained 상한을 사용합니다(`optimized_prefix_replay.py:83`). 마지막 정상 sidecar에서 retained는 약 1MiB였고 실패 시 정리됐습니다. 임시 Python 객체·libpq·PG backend 메모리나 Windows pagefile 크기는 실행 중 계측되지 않았으므로 프로세스 메모리 증가 가능성을 배제할 수는 없습니다.

### D. prepared statement·cursor: 무제한 보존의 직접 근거 없음

400행 PK query는 행 값이 바뀌어도 같은 placeholder SQL을 재사용합니다. 연결은 psycopg 기본 준비 정책을 변경하지 않습니다. Psycopg는 기본 prepared statement 개수에 한도를 두고 오래된 것을 해제합니다. 이것을 **batch마다 무한한 statement가 생긴 원인**으로 단정할 근거는 없습니다. [Psycopg prepared statements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html), [연결의 prepared_max](https://www.psycopg.org/psycopg3/docs/api/connections.html)

최종 검증은 named cursor context manager를 사용하지만 이번 실행은 그 단계에 도달하지 않았습니다. 따라서 최종 cursor/정렬은 이번 prefix 실패의 직접 원인으로 지목하지 않습니다.

## 3. 별도 확인된 최종 검증의 정렬·인덱스 위험

**최종 검증 전에 해결해야 할 P1 위험입니다. 이번 공간 실패와는 구분합니다.**

| 위치 | 현재 SQL / 전달 방식 | 위험 |
|---|---|---|
| `ordered_verify.py:141` | 한 series의 payload를 `ORDER BY time COLLATE "C"`로 named cursor 읽기 | 기본 collation PK의 정렬을 직접 활용하지 못하고 해당 series의 payload를 정렬할 수 있음 |
| `ordered_verify.py:163` | trace payload 전체 `ORDER BY digest COLLATE "C"` | default collation trace PK와 불일치 시 별도 정렬 및 임시 파일 가능 |
| `local_restore.py:498` | 원본 digest inventory `ORDER BY digest COLLATE "C"` | 같은 불일치 위험. 키만 읽으므로 payload sort보다 규모는 작음 |
| `indexed_resume.py:27`, `:81`, `:136` | 매핑은 exact count SELECT 4개에 한정; `cursor()`는 원래 연결로 위임 | 확인된 count Index/Index Only Scan 증거가 최종 named cursor query까지 보호하지 않음 |

PG는 속성이 같아도 서로 다른 collation 객체를 같은 것으로 취급하지 않으며 인덱스 열마다 하나의 collation을 지원합니다. 실제 count gate는 키 열과 PK가 **`pg_catalog.default` 객체**를 쓰고 database locale가 C임을 확인했습니다. 따라서 문자열 `COLLATE "C"`를 명시하는 최종 query는 별도의 실행 계획 증거가 필요합니다. [PG17 collation 객체](https://www.postgresql.org/docs/17/collation.html), [PG17 인덱스와 collation](https://www.postgresql.org/docs/17/indexes-collations.html)

`_series_statement`의 재귀 seek는 이미 기본 열 정렬을 사용하고 마지막 distinct series 키만 C로 정렬합니다(`ordered_verify.py:90`). 이 경로를 전체 1,701만 payload의 sort라고 표현하면 틀립니다. 위험은 **series별 payload 및 trace payload 정렬**입니다.

후속 수정 후보는 owned PG17 UTF8/default C와 key/index collation·실제 EXPLAIN이 확인된 경우에만 최종 고정 query에도 default-column 정렬을 적용하는 별도 명시 adapter입니다. named cursor 전달도 포함해야 합니다. 기존 Python UTF8 byte order 검사, canonical codec, 모든 행·원본 참조 및 전체 ordered hash를 유지해야 하며 locale 조건이 다르면 거부해야 합니다. 이번 조사에서는 query를 변경하거나 실행 계획을 새로 실행하지 않았습니다.

## 4. 다음 실행 전 최소 진단과 재계획

1. **새 전체 복원 재시도는 중단 상태로 유지합니다.** 원인 미확정인 같은 공간 실패가 두 차례 반복됐습니다. 종료 후 free가 회복됐다는 사실만으로 다시 4시간 작업을 시작하지 않습니다.
2. 별도 짧은 진단 설계를 먼저 정합니다. owned PID의 private bytes/commit·handle 수, Windows pagefile 크기, owned cluster의 논리 크기와 파일 데이터 할당량, TEMP/stage/TOAST/WAL의 크기, root TEMP 경로의 owned 제어 파일을 **같은 시각**에 표본 계측해야 합니다. 전체 드라이브 스캔 대신 사전에 정한 경로·PID만 봅니다. 실행 중 삭제 열린 파일은 경로 stat만으로 설명할 수 없으므로 해당 handle 근거가 필요합니다.
3. query 임시 파일에는 제한된 `log_temp_files` 계측을 사용할 수 있지만 명시 TEMP table 계측을 대신하지 않습니다. source SQL·payload·비밀값은 로그에 남기지 않습니다. PG17의 `pg_stat_database.temp_bytes`나 명시 stage 할당량은 별도 허용된 진단 단계에서만 읽습니다. 이번에는 실행하지 않았습니다.
4. 최종 named cursor query의 unforced EXPLAIN과 collation 계약을 보완합니다. sort가 남아 있으면 필요한 디스크·시간을 별도 예산에 반영하되 기존 보호 한도를 높이지 않습니다.
5. 원인이 Windows 임시 stage 할당으로 확인되면 bounded native COPY stage의 처리/정리 수명만 변경합니다. 원본·400행/16MiB framing·frozen codec·exact keys·canonical 비교·최종 전체 hash gate는 변경하지 않습니다. 어떤 대안도 fixture 통과만으로 실제 복원 완료라 하지 않습니다.

**종료 판단:** 실제 원본 17,014,175행·8,071개 원본 참조와 전체 ordered hash가 일치하고 supervised child 및 클러스터 종료를 확인한 새 RESULT가 있어야 복원 통과입니다. 현재는 원본과 기존 partial DB가 보존된 **미완료** 상태입니다.
