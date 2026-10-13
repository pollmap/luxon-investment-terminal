# 중지된 테스트 클러스터 보존 압축 — 실제 1회 실행

2026-10-13 KST. 고정 대상의 파일 바이트를 보존하는 작업이며, 금융 데이터의 전체 복원 검증 작업은 아닙니다.

**30분 시간 가드로 중단됐습니다. 원본을 정리할 수 있는 전체 보존 증거는 확보하지 못했습니다.** 원본·기존 덤프·암호화 사본·부분 복원 결과를 유지했고, 같은 실행을 자동으로 반복하지 않았습니다.

## 실행과 예산

```text
.venv/Scripts/python.exe -u ops/market/preserve_stopped_restore.py --run --run-id 8bd7ac7221af4b3cbab233305fa53654 --estimated-output-bytes 6442450944
```

- 고정 원본: `work/restore-daily-20261010-v106/cluster-data`
- 출력: `work/stopped-restore-archives/8bd7ac7221af4b3cbab233305fa53654`
- 도구 SHA-256: `19d432eed1359ff0c7aaa1b6f1c221762132e80d98b48a8efbcc150c4fc9dbdd`
- 소유권 문서 SHA-256: `10a1ee205e660b708882d8ed7f19a1e06a2b8a33e653e79239b166ae63b670ed`
- 한도: 시간 1,800초, 출력 및 전체 드라이브 여유 감소 각각 6GiB, 최소 여유 4GiB
- 예상 출력: 6GiB
- 실행 직전 관측 여유: 16,012,640,256바이트. 예상 출력과 최소 보존 공간의 합보다 컸습니다.
- 새 복원·PG 시작·DB 또는 서비스 변경·삭제: 없음

## 실제 종료 결과

| 항목 | 결과 |
|---|---|
| 종료 코드 | `1` |
| 상태 | `interrupted` |
| 정확한 오류 | `Refused: archive_time_budget_exhausted` |
| 가드 기준 경과 | **1,800.047초** |
| 완료 파일 기록 | **361개**. 사전 감사의 전체 대상은 1,732개입니다. |
| 완료 파일 원본 바이트 합 | 11,779,858,962바이트 |
| 전체 원본 논리 크기 | 50,712,188,923바이트 |
| 부분 압축본 | 1,821,692,481바이트 |
| 최종 확인 여유 | 13,240,545,280바이트 |
| 전체 파일 바이트 검증 | **미통과**, `file_bytes_verified=false` |
| DB 전체 복원 검증 | **미통과**, `database_restore_verified=false` |
| 검증 manifest / 완료 압축본 | 생성되지 않음 |
| 부분 결과 보존 | `partial_preserved=true` |
| 원본 삭제 | `source_deleted=false` |
| 자동 재시도 | 없음 |

시간 가드는 실제 읽기·쓰기 도중 작동했습니다. gzip 종료 처리에서도 같은 가드가 거부했으므로 부분 파일을 완성된 gzip 아카이브처럼 사용하면 안 됩니다. checkpoint는 관측·파일 해시 기록이며 중단된 gzip 스트림에 이어 쓸 수 있다는 의미가 아닙니다.

완료 기록 361개에는 원본을 읽을 때 계산한 파일 SHA가 있으나, **압축본 전체를 다시 읽는 검증·모든 1,732파일 대조·전체 gzip SHA·원본 전후 목록 일치**는 통과하지 못했습니다. 이 부분 결과를 독립 보관본으로 승격하거나 원본 삭제 근거로 사용하지 않습니다.

## 보존과 증거

- 원본 폴더는 존재하며 PostgreSQL 버전은 `17`, 종료 후 확인한 `postmaster.pid`는 없습니다.
- 기존 덤프 크기 2,372,779,444바이트, 암호화 사본 크기 2,372,779,770바이트는 유지됐습니다. 이번 종료 확인은 존재·크기 확인이며 해당 파일을 전체 재해시하지 않았습니다.
- 도구는 고정 원본을 읽고 별도 출력 폴더에만 쓰므로 기존 부분 compact 복원 결과를 변경하지 않았습니다.
- 소유권 문서의 `source_content_equality_verified=false`를 그대로 유지했습니다. 과거 스키마·행 수 검사 결과를 원본 내용 일치나 이번 작업 성공으로 바꾸지 않았습니다.
- checkpoint SHA-256: `ca655806545561c73179f01dd64f521a9f3100b916fdbec2ef72a39b1ea451cf`

인증값 없는 상세 증거는 ignored 작업 경로에 보존했습니다.

| 파일 | 내용 |
|---|---|
| `work/lucian-v136/preserve-actual-run.json` | 실행 ID·고정 대상·출력 예상치·시작 기록 |
| `work/lucian-v136/preserve-actual-result.json` | 종료 코드·가드 시간·상태·출력 크기·checkpoint SHA·보존 확인 |
| `work/lucian-v136/preserve-8bd7ac7221af4b3cbab233305fa53654.log` | 실제 종료 로그 |
| `work/stopped-restore-archives/8bd7ac7221af4b3cbab233305fa53654/checkpoint.json` | 완료 파일별 SHA·중단 사유·보존 상태 |
| `work/stopped-restore-archives/8bd7ac7221af4b3cbab233305fa53654/cluster.tar.gz.partial` | 미완료 압축 스트림. 보존 대상으로 유지 |

이번 관측에서는 디스크 한도보다 시간 한도가 먼저 도달했습니다. 읽기·압축·SHA·여유 공간 검사 비용을 분리 측정하지 않았으므로 병목을 특정 요소 탓으로 단정하지 않습니다. 다음 실행은 처리 비용과 충분한 전체 검증 시간을 다시 산정한 뒤 별도로 결정하며, 이번 기록만으로 실행 한도를 높이거나 같은 작업을 재시작하지 않습니다.
