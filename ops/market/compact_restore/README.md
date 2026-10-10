# 시계열·공유 출처 압축 복원 구성요소

검증한 실험 코드를 유지보수 가능한 패키지로 옮겼습니다. **운영 실행을 조율하는 도구는 아직 없습니다.** 이 패키지만으로 운영 백업의 독립 복원, 테이블 전환, 공간 회수가 완료되지는 않습니다.

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

`VerifiedArchive`는 호출자가 전체 파일을 해시 검증한 결과와 전후 stat을 받습니다. stat 일치는 전체 해시 검증을 대신하지 않습니다. `restore_prefix`도 아카이브 전체 해시를 전후 확인합니다. TOC는 정확히 `market_source_traces`와 `market_time_series`의 TABLE DATA 두 항목을 그 순서로 선택해야 합니다. 덤프의 SQL은 실행하지 않습니다.

`Manifest.codec_sha256`는 이 패키지의 파서·체크포인트 코드와 실제 `source_trace_store.py`에 묶입니다. 실험 경로에서 생성한 체크포인트는 패키지 이전 후 재사용할 수 없습니다. 새 아카이브·원본 대조 증거로 새 manifest를 만들어야 합니다.

기본 보호값은 배치 400행·16MiB 이하, 복원 볼륨의 계획 증가량 외 6GiB 여유입니다. `PgBatchSink`는 지정한 새 로컬 후보 DB·포트·data directory만 받아 연결 identity를 검사합니다. DB의 잠긴 호출·COMMIT과 전체 검증의 중단은 호출자가 별도로 보장해야 합니다. 프로세스 감독만으로 DB 트랜잭션의 hard deadline까지 보장하지는 않습니다.

앞으로 필요한 운영 실행 도구는 현재의 쓰기 차단과 최신 백업 증거, 실제 대상 identity, 고정 TOC 생성, 전체 원본 검증, 취소·자원 상한, 독립 복원 결과를 함께 묶어야 합니다. 인증·DSN·수신 시각·해시를 꾸며 넣거나 수동 성공 receipt로 이 조건을 대신하지 않습니다. 공유 출처 교체·삭제, 운영 전환·원본 삭제는 이 패키지의 범위 밖입니다.

순수 회귀검사:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_market_compact_stream.py tests/test_market_compact_checkpoint.py tests/test_market_compact_supervisor.py tests/test_market_compact_verify.py
```

테스트는 `fixture_non_production`입니다. 실제 PostgreSQL·운영 원문을 요구하지 않습니다. supervisor 검사만 작은 Python 자식을 실행하며 PG를 시작하지 않습니다. 원래 격리 PG 실험 결과와 원본 실험 파일은 `work`에 그대로 보존합니다.
