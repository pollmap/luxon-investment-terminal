# 독립 복원 재생 경로 검토

2026-10-13 KST. 소스와 기존 RESULT만 읽었습니다. 새 복원·PG 시작·DB 질의·전체 해시·전체 디스크 스캔·앱 소스 변경은 하지 않았습니다.

## 실제 결과와 해석

대상은 `resume-a900a5ff368341778d56c42c75629562`입니다. 실제 복원 CLI는 **exit 1**이고, 이를 실행한 PowerShell wrapper의 exit 0은 성공 증거가 아닙니다.

- `restore_verified=false`, `data_equal=false`, `state=interrupted`, `phase=prefix_restore`.
- 정확한 기존 저장량은 시계열 15,282,400행·원본 참조 8,071건입니다. 이 count는 실제 PK keyset으로 일치했습니다.
- 이번 재생에서 검증한 prefix는 4,262,400행이며, 전체 정렬 해시는 끝나지 않았습니다.
- 6,156.422초를 기준으로 한 **전체 시도 평균**은 약 692행/초입니다. 초기 count·아카이브 확인·원본 trace 재생 시간도 포함하므로 prefix 전용 처리속도 측정값은 아닙니다.
- 같은 전체 평균을 단순 연장하면 기존 15,282,400행 prefix에 약 6.13시간, 전체 17,014,175행에 약 6.83시간입니다. 최종 해시는 별도이며, 개선 후 예상시간이나 실제 수행계획으로 사용하지 않습니다.
- 중단 사유는 `disk_reserve_exhausted`입니다. 전역 여유가 4GiB 아래로 내려갔고, 복원 cluster 자체의 증가량은 0으로 기록됐습니다. 빌드·cluster 밖 임시 파일·OS pagefile 중 무엇이 원인인지는 분해하지 않았습니다.

## 코드에서 확인한 경로

| 구간 | 확인 내용 | 근거 |
|---|---|---|
| count | 실제 `traces_pkey` Index Scan, `rows_pkey` Index Only Scan의 최초·다음 페이지 계획이 통과했습니다. | RESULT `count_index_contract` |
| 400행 PK 조회 | `(series,time) IN ((%s,%s),...)`로 정확한 키 쌍을 조회합니다. 별도 `COLLATE`가 없어 해결한 count collation 결함과 같지 않습니다. `CASE + sum(octet_length(payload)) OVER()`는 전송 전 payload 한도를 검사합니다. **이 replay 쿼리의 실제 실행계획은 미측정입니다.** | `replay_batch.py:13`, `:58`, `:61`, `:63` |
| 임시 stage | 매 400행마다 새 transaction에서 두 TEMP TABLE과 identity를 생성합니다. decode 후 `TRUNCATE RESTART IDENTITY`를 수행하고 COMMIT 때 DROP합니다. 정상 prefix 재생은 실제 rows·traces·checkpoint를 쓰지 않지만 임시 relation의 catalog/파일 작업은 반복됩니다. | `replay_batch.py:39`, `:43`; `copy_stream.py:225`, `:247`, `:271`, `:314` |
| 참조·canonical 비교 | batch마다 새 `TraceLookup`가 생성됩니다. 256건·16MiB 캐시는 `TraceRecord`를 보관하며, 참조 해석 때 `verified_value()`가 SHA·JSON 파싱·canonical 인코딩을 다시 수행합니다. 양쪽 행을 각각 `row_identity()`로 확장·인코딩해 비교합니다. | `replay_batch.py:75`, `:79`; `checkpoint.py:223`; `copy_stream.py:376`, `:382`, `:394`; `source_trace_store.py:56` |
| 파이프·메모리 | stdout은 64KiB × 4 chunk queue이며 readline은 최대 16MiB+1입니다. 5.3GB stdout 누적량은 전송 총량이며 메모리 보유량이나 임시 파일 크기가 아닙니다. parser batch·trace cache는 제한돼 있지만 Python 객체 확장량·RSS·pagefile은 이번에 측정하지 않았습니다. | `restore_supervisor.py:48`, `:158`, `:210`, `:267`; `copy_stream.py:58`, `:112` |
| cluster 밖 파일 | control command만 `TemporaryFile()` stdout/stderr를 사용하며 기본 4MiB/1MiB 상한과 ExitStack 정리가 있습니다. 대량 `pg_restore` stdout 경로에는 디스크 spool이 없습니다. 이 코드만으로 전역 8GiB 감소를 해당 임시 파일 탓으로 볼 근거는 없습니다. | `local_restore.py:309`, `:330`, `:343`; `restore_supervisor.py:206`, `:267` |

## 다음 구현 후보 — 두 가지로 제한

1. **Prefix replay의 typed TEMP stage를 소유 connection 안에서 재사용합니다.** 원래 PostgreSQL COPY decoding과 400행·16MiB 제한, exact PK·payload 비교는 유지합니다. 세션 한정 stage에 `ON COMMIT DELETE ROWS` 또는 제한된 DELETE를 적용해 매 batch CREATE/DROP 및 TRUNCATE relfilenode 재생성을 줄이는 별도 어댑터를 검토합니다. raw COPY를 Python SQL로 바꾸거나 배치 한도를 높이지 않습니다. stage 잔류·rollback·취소·identity 입력 순서·다음 batch 오염 회귀가 필수입니다.
2. **Prefix 전용 검증 참조 cache를 재사용합니다.** 소유 connection·재생 phase에 한정해 기존 256건/16MiB 제한을 유지하고, 검증된 immutable record의 해석을 반복하지 않도록 합니다. decoded 객체의 실제 메모리 예산·수정 오염 방지도 포함합니다. 누락/변조/동일 digest의 다른 payload를 계속 거부하고, append 후 독립 재조회와 최종 전체 ordered hash는 그대로 둡니다. 전역 서비스 cache나 주어진 원본의 SHA·manifest를 바꾸지 않습니다.

이는 코드상 반복 비용을 줄이는 후보이며 692행/초의 원인이 실측으로 확정됐다는 뜻은 아닙니다. 다음 실행을 승인하기 전 DDL/COPY/PK 조회/trace 해석/canonical 비교/COMMIT의 batch 시간과 RSS·전역 여유·소유 cluster 증가를 분리해야 합니다. 기존 frozen codec 파일을 수정해 manifest가 어긋나게 만들거나, 진행 파일을 DB 검증으로 승격해 재생을 건너뛰면 안 됩니다.

이번 실행과 관련해 새 재시도는 하지 않습니다. 원본·부분 결과를 보존하며, 전체 17,014,175행·정렬 해시·원본 참조 검증과 정상 종료가 통과해야 독립 복원 완료입니다.
