# 백업·독립 복원 보완

이번 변경은 도구와 회귀 검사다. 실제 VPS 백업·복원·삭제를 실행한 증거가 아니다.

## 부분 파일 정리

- 정리할 부분 파일이 없으면 정상 무작업으로 끝낸다.
- 오프사이트 전용 복구 증거를 서버에 없는 dump의 검증으로 사용하지 않는다.
- 목록 작성·재확인·삭제 사이 파일이 사라진 경우만 이미 부재로 처리한다. 권한 오류·파일 교체·내용 변경은 차단한다.
- 제거한 할당량에는 실제 이 실행에서 제거한 파일만 포함한다.

## VPS 파일을 만들지 않는 백업

먼저 `.venv/Scripts/python.exe -m ops.market.stream_backup`으로 비실행 기본값을 확인한다.

명시적 실행은 `.venv/Scripts/python.exe -m ops.market.stream_backup --run --max-bytes 6442450944 --max-seconds 7200`이다. 고정된 주식 DB에서 읽기용 `pg_dump`를 실행하고 SSH stdout을 PC의 새 개인 디렉터리에 저장한다. 서버 정기 백업과 같은 lock을 사용한다. 원격 timeout도 적용한다.

- PC 사전 여유는 최대 dump 크기＋4GiB 이상이어야 한다.
- `work/backups/streaming/<실행 ID>`에 `.partial`을 배타적으로 만들며 사용자/SYSTEM 접근만 허용한다.
- producer 정상 종료, 수신 SHA256 재대조, custom dump 식별과 `pg_restore --list` 통과 후에만 `.dump`로 원자적 전환한다.
- 실패한 부분 결과와 고정 오류 코드를 보존한다. 원격 stderr나 비밀값을 기록하지 않는다.
- 전송 완료는 전체 DB 복원 성공이 아니다. `restore_verified=false`, `source_content_equality_verified=false`를 유지한다.
- 실제 전체 복원과 최신 변경분 검증 전에는 기존 서버 dump·DB·원본을 삭제하지 않는다.

## 기존 독립 복원 재개

기존 archive의 codec SHA에 포함된 `checkpoint.py`는 수정하지 않았다. 동일 archive·manifest·codec·DB checkpoint 계약을 유지한다.

`ops.market.compact_restore.resume_restore`의 `--preflight`는 기존 `--run`과 같은 work-directory/archive/archive-receipt/source-report 및 공간 예산 인자를 받는다. 이 모드는 제한된 메타데이터·파일 크기만 읽고 전체 archive hash·PG 시작·새 파일 생성은 하지 않는다. `checkpoint_reload_required=true`를 유지하므로 사전 검사로 데이터 일치를 주장하지 않는다.

- 절대 여유 4GiB, 복원 디렉터리 논리 크기 증가, 전체 cluster 크기, 실행 시간을 따로 제한한다.
- C: 전체 감소량은 `drive_growth_bytes`로 기록하며 복원 작업 증가로 오인하지 않는다. 외부 작업이 공간을 소비해도 4GiB 절대 하한에서는 중단한다.
- `processed`는 이번 입력 처리 위치, `committed`는 DB에서 확인한 checkpoint 또는 성공적으로 반환된 commit 위치, `verified_prefix`는 원문과 대조한 기존 prefix 범위다.
- commit 직후 timeout 등 불확실한 종료는 `checkpoint_reload_required=true`이며 파일의 마지막 숫자를 재개 근거로 사용하지 않는다.
- 전체 행 수·ordered hash·원본 참조 검사가 모두 끝난 경우만 `ordered_hash_verified=true`가 된다. 최종 정상 종료와 입력 불변성까지 통과해야 `restore_verified=true`다.

권장 실행 예산은 기존 4시간·추가 6GiB·전체 cluster 16GiB다. preflight를 통과하지 못하면 실행하지 않는다. 같은 실패를 자동 반복하지 않으며 원본과 부분 결과를 보존한다.

## 이번 검증

- 부분 파일 정리·정기 백업·보존 정책: 기존 통합 실행에서 관련 30개 회귀 검사를 통과했다.
- 복원 예산·체크포인트 기록·사전 검사·스트리밍 백업: 최종 집중 실행 73개 통과, 실패·오류·건너뜀 0개. `storage-recovery-tests.xml`에 기록했다.
- 변경한 Python 파일과 테스트의 Ruff 검사를 통과했다.
- fixture 바이트와 가짜 프로세스를 사용한 검사이며 실제 VPS 백업, 전체 복원, 정리, 공개 배포의 통과 기록은 아니다.

## 기존 자료의 읽기 사전 검사

`storage-restore-preflight.json`의 실제 읽기 사전 검사는 통과했다. 입력과 작업 경로에는 기존 도구의 계약대로 절대 경로를 사용했다.

- 대상: `work/target-restore/82cf96ad6b684cfab19678431ad8f171`.
- 입력: `work/storage-v117/runs/49521be9a50149bba6a66c21342c3bc1`의 `market-targets.dump`, `archive-receipt.json`, `source-report.json`.
- 기대 행 수: 시계열 17,014,175행, 원본 참조 8,071행.
- 검사 종료 여유 공간: 12,527,054,848 bytes. 클러스터 논리 크기: 9,500,023,613 bytes.
- 추가 예산 6,442,450,944 bytes와 여유 하한 4,294,967,296 bytes를 합친 사전 요구량: 10,737,418,240 bytes.
- 전체 클러스터 상한 17,179,869,184 bytes, 실행 시간 상한 14,400초.

사전 검사는 archive 전체 해시·DB checkpoint·전체 행 일치를 재검증하지 않았다. `checkpoint_reload_required=true`, `archive_content_verified=false`, `restore_verified=false`, `production_contacted=false`다. 빌드나 다른 작업 뒤 공간이 달라질 수 있으므로 실제 실행도 같은 사전 검사를 다시 수행한다. 복원이나 스트리밍 백업은 이 담당 작업에서 실행하지 않았다.
