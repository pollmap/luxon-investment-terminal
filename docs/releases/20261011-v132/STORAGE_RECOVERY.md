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

## 이후 승인된 복원 1회와 중단 결과

위 사전 검사 뒤 별도 승인을 받아 2026-10-11 22:45:31 KST에 같은 입력·예산으로 독립 복원 재개를 한 번 실행했다. 시작 직전 사전 검사는 통과했고, 당시 여유는 11,015,790,592 bytes였다. 약 135.6초 뒤 실행 사전 검사 단계에서 중단됐다.

- 결과: `storage-restore-run-result.json`, 시작/종료: `storage-restore-run-invocation.json`, `storage-restore-run-exit.json`.
- `phase=preflight`, `restore_verified=false`, `cluster_stopped=true`, `processes=[]`.
- archive 전체 재해시와 입력 메타 대조 다음 단계까지 도달했다. DB 시작·새 시도 디렉터리 생성·행 복원 전 중단됐으며 기존 클러스터 논리 크기는 9,500,023,613 bytes로 유지됐다.
- 당시 보고기에 원시 예외 종류가 없어 최초 실패를 Windows 메모리 오류로 확정하지 않는다. 원문·비밀값을 출력하지 않는 도구 진단에서 `postgres`와 `pg_restore` 17.11의 단독 버전 조회는 모두 정상 종료했다.
- 동일 coordinator의 읽기 재현에서는 첫 버전 조회가 `command_deadline_exceeded`로 실패했다. 5초 제한에 디스크 예산 검사 시간이 포함돼 제한을 넘는 경로를 확인했다. 증거: `storage-restore-coordinator-diagnostic.json`.
- 버전 확인 제어 명령만 30초로 조정했다. 전체 4시간·추가 6GiB·클러스터 16GiB·절대 여유 4GiB는 변경하지 않았다. archive 검증 통과·사전 검사 세부 단계·예외 종류와 숫자 errno/winerror를 기록하도록 보완했다. 예외 문장·SQL·자식 도구 출력은 저장하지 않는다.
- 보완 회귀 검사 6개와 Ruff 통과: `storage-control-timeout-tests.xml`.

후속 읽기 검사는 C: 여유가 추가 6GiB＋하한 4GiB 요구량보다 작아 coordinator 생성 단계에서 차단됐다. 따라서 30초 변경의 실제 coordinator 도구 검사는 아직 통과하지 않았고, 전체 복원 두 번째 실행은 하지 않았다. 이 상태에서 공간 하한을 낮추거나 자동 재시도하지 않는다. 원본, 부분 결과, 운영 DB는 보존한다.

메모리 관측도 별도 보존했다(`storage-local-memory-diagnostic.json`). 22:53:57 KST의 물리 메모리 사용률은 93%였지만, 이는 최초 실패 원인을 확정하는 증거가 아니다. 전송용 스트리밍 백업은 여전히 실행하지 않았다.

## 후속 이미지 정리 가능성 확인

기존 캐시·정적 배포본 정리 뒤 23:06 KST 서버 관측은 전체154,894,188,544B·사용144,499,089,408B·가용10,378,321,920B, df94%였다. v132 API·worker가 실행 중이고 DB·Redis·OpenAlgo 및 다른 서비스의 기존 컨테이너가 유지됐다. `hongong-learning-api-1`의 unhealthy는 점검 전에도 존재한 상태이며 이 작업에서 수정하지 않았다.

5분 제한으로 비활성 주식 이미지의 추가 회수 가능성을 확인했다. 보존 대상은 v132 `9873c59c39ba`, v131 `8357523f7db5`, v131 호환 롤백 `4903f168f390`, v130 `2a3973b0f524`다. web `8d06235ba8d0`와 OpenAlgo `f46ef529b0cc`는 여러 태그가 같은 이미지 ID를 공유하고 컨테이너 참조도 있어 태그 수와 표시 크기를 곱하지 않는다.

`docker system df -v`가45초, 후속 image 목록이30초 제한을 넘겨 고유 레이어·배포 참조 대조를 끝내지 못했다. 그러므로 추가 이미지의 안전한 실질 회수량은 미확인이다. 이번 후속 확인에서는 이미지 삭제·prune·볼륨 변경을 하지 않았다. 검증된 삭제 후보가0건이라는 것은 비활성 이미지 자체가 없다는 뜻이 아니다.
