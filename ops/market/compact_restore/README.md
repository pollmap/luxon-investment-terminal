# 시계열·공유 출처 압축 복원 구성요소

검증한 실험 코드를 유지보수 가능한 패키지로 옮겼습니다. 원본 스냅샷 검증과 **새 로컬 DB로의 명시적 복원 실행**을 지원합니다. 백업 중 지정한 수집기를 멈추는 제어기도 포함합니다. 이 패키지의 복원 성공만으로 운영 테이블 전환과 공간 회수가 완료되지는 않습니다.

저장소 루트에서 다음 명령은 준비 상태만 출력합니다. DB 연결·프로세스 실행·파일 쓰기를 하지 않으며 `--run`·`--apply` 옵션은 받지 않습니다.

```powershell
.venv/Scripts/python.exe -m ops.market.compact_restore
```

| 모듈 | 역할 |
| --- | --- |
| `archive_receiver.py` | 바이너리 덤프를 새 파일로 받고 용량·시간·종료코드·SHA를 검사합니다. 실패한 부분 파일을 보존하며 수신 성공을 복원 성공으로 표시하지 않습니다. |
| `copy_stream.py` | 두 테이블의 COPY 구획·순서를 제한하고, 필드 해제는 PostgreSQL의 COPY에 맡깁니다. 기존 공유 출처 전체를 보존하며 시계열만 압축합니다. |
| `checkpoint.py` | 같은 트랜잭션 안에서 작은 배치와 체크포인트를 기록합니다. 재개 시 보존된 앞부분의 모든 행을 대조합니다. |
| `restore_supervisor.py` | 고정 `pg_restore --file=-` 명령의 바이너리 출력을 제한된 큐로 전달하고 실제 종료코드를 확인합니다. 시간·출력 상한과 취소 시 자신이 시작한 자식만 종료합니다. |
| `ordered_verify.py` | 읽기 전용 스냅샷에서 C 정렬 기준으로 전체 키·논리 JSON·기존 공유 출처·인덱스를 독립 대조합니다. |
| `source_snapshot.py` | 두 원본 표의 구조·주인·PK·의존성을 확인하고 실제로 내보낸 PostgreSQL 스냅샷에서 모든 값과 원래 출처 목록을 기록합니다. 호출자가 연결과 외부 쓰기 중단을 관리합니다. |
| `local_restore.py` | 해시로 연결된 아카이브·수신 기록·원본 검증 결과를 새 로컬 PG17에 복원하고 전체 대조 후 해당 클러스터를 종료합니다. 명시적 `--run` 없이는 연결하지 않습니다. |
| `fenced_backup_host.py` | 지정한 VPS 릴리스·컨테이너를 확인해 수집기를 멈추고 백업 스냅샷을 유지합니다. 스냅샷 해제와 수집 재개를 구분하며 실패·연결 종료 때 같은 작업자를 복구합니다. 테이블 전환·삭제 기능은 없습니다. |

`VerifiedArchive`는 호출자가 전체 파일을 해시 검증한 결과와 전후 stat을 받습니다. stat 일치는 전체 해시 검증을 대신하지 않습니다. `restore_prefix`도 아카이브 전체 해시를 전후 확인합니다. TOC는 정확히 `market_source_traces`와 `market_time_series`의 TABLE DATA 두 항목을 그 순서로 선택해야 합니다. 덤프의 SQL은 실행하지 않습니다.

`Manifest.codec_sha256`는 이 패키지의 파서·체크포인트 코드와 실제 `source_trace_store.py`에 묶입니다. 실험 경로에서 생성한 체크포인트는 패키지 이전 후 재사용할 수 없습니다. 새 아카이브·원본 대조 증거로 새 manifest를 만들어야 합니다.

기본 보호값은 배치 400행·16MiB 이하, 복원 볼륨의 계획 증가량 외 6GiB 여유입니다. `PgBatchSink`는 지정한 새 로컬 후보 DB·포트·data directory만 받아 연결 identity를 검사합니다. DB의 잠긴 호출·COMMIT과 전체 검증의 중단은 호출자가 별도로 보장해야 합니다. 프로세스 감독만으로 DB 트랜잭션의 hard deadline까지 보장하지는 않습니다.

### 백업 작업자 제어

`python -m ops.market.compact_restore.fenced_backup_host`의 기본 호출은 오프라인입니다. 실제 실행은 검토된 Linux 호스트의 `--run`과 stdin 설정이 필요합니다. 현재 구현은 `20261010-history-contract-v116` 및 해당 이미지로 고정되어 있으며 다른 배포를 자동 허용하지 않습니다.

`verify`는 같은 exported snapshot의 전체 자료를 검증합니다. `release`는 DB 잠금만 풀고 수집기 중단과 배포 잠금은 유지합니다. `finish`와 `abort`가 같은 원래 수집기를 복구합니다. stdin 종료·절대시한 초과도 복구 경로로 들어가며, 호스트 프로세스를 강제 종료한 경우 보존된 run ID로 `--run --recover <run-id>`를 명시적으로 실행해야 합니다. 재시작을 확인하지 못하면 성공으로 표시하지 않습니다.

제어기 자체는 수동 수집 스크립트·타이머의 부재를 증명하지 않습니다. 실행자는 별도로 대상 writer·API 쓰기 경로를 확인해야 합니다. 앱 조회는 유지할 수 있지만, 테이블 교체와 오래된 표 정리는 별도의 최신 백업·복원·전체 대조를 요구합니다.

### 로컬 복원 명령

기본 조회는 오프라인입니다.

```powershell
.venv/Scripts/python.exe -m ops.market.compact_restore.local_restore
```

실행에는 같은 원본 스냅샷에서 만들어진 아카이브와 증거 파일의 **절대 경로**, 확인한 공간 예산이 모두 필요합니다.

```text
python -m ops.market.compact_restore.local_restore --run
  --archive <absolute-custom-archive-path>
  --archive-receipt <absolute-bound-receiver-json-path>
  --source-report <absolute-exported-snapshot-report-path>
  --max-growth-bytes <verified-byte-budget> --max-seconds <1-to-21600>
```

고정 대상은 `127.0.0.1:54433`, 새 `work/target-restore/<uuid>/cluster-data`, `market_compact_restore_candidate` DB입니다. 실행기는 기존 클러스터를 재사용하거나 삭제하지 않습니다. 데이터 디렉터리·포트·프로세스 소유 기록이 다르면 중단합니다. 비밀번호는 실행마다 생성하며 저장소에 올리지 않습니다. PG17 실행 파일은 `work/tools/postgresql-restore/pgsql/bin`에 있어야 합니다.

수신 기록의 `source_report_sha256`는 원본 검증 JSON 파일 전체 SHA와 일치해야 합니다. 검증 도구가 만든 실제 값을 사용하며 성공 필드를 수동으로 채우지 않습니다. 두 표의 모든 원래 출처와 C 정렬 논리값, 아카이브 불변, 독립 복원본의 PK 및 종료를 함께 확인합니다. fixture 입력은 복원이 성공해도 `production_proof=false`입니다.

시간·계획 증가량·6GiB 최소 여유를 검사하며 DB 호출은 감시 스레드가 취소합니다. 복원 자식과 자신이 만든 PG만 종료합니다. OS 파일 동기화 자체를 강제 중단하는 보장은 없습니다. 실패한 아카이브·부분 복원·진단은 보존하며 자동으로 성공하거나 운영 원본으로 복구하지 않습니다. 현재 실행기는 새 복원만 지원합니다. 중단 후 재개 원리는 하위 모듈에 있지만 이 CLI에서 이전 클러스터를 이어 쓰지는 않습니다.

앞으로 필요한 운영 실행 도구는 현재의 쓰기 차단과 최신 백업 증거, 실제 대상 identity, 독립 복원 결과를 함께 묶어야 합니다. 공유 출처 교체·삭제, 운영 전환·원본 삭제는 이 패키지의 범위 밖입니다.

순수 회귀검사:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_market_compact_stream.py tests/test_market_compact_checkpoint.py tests/test_market_compact_supervisor.py tests/test_market_compact_verify.py
```

테스트는 `fixture_non_production`입니다. 실제 PostgreSQL·운영 원문을 요구하지 않습니다. supervisor 검사만 작은 Python 자식을 실행하며 PG를 시작하지 않습니다. 원래 격리 PG 실험 결과와 원본 실험 파일은 `work`에 그대로 보존합니다.
