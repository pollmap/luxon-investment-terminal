# 보존·용량·백업 주기 실행 점검

2026-10-09~10 KST. 최초 점검·코드 검증과 이후 운영 반영을 구분합니다. 각 절의 관측 버전을 기준으로 읽으며 이전 검증 기록도 보존합니다.

## 10월 10일 04:42 KST — v108 실패 partial 자동 정리

기존 정상 덤프 보존 정책은 유지하고 `backup.py`에 실패 partial만 처리하는 경로를 추가했습니다. 실제 격리 복원·암호화 검증 receipt, 서버의 정상 덤프 SHA-256·manifest, 백업 잠금, 열린 파일, 48시간 경과와 복구본보다 이전 시각을 함께 검사합니다. 안전 조건을 만족하지 못하면 파일을 보존합니다. 정상 덤프나 원본·PC 복원 디렉터리를 정리하는 기능은 아닙니다.

04:20 시작한 정기 백업이 끝난 뒤 네 partial을 제거해 할당 공간 544,731,136바이트를 회수했습니다. 그중 두 파일은 빈 파일입니다. 04:42에 정상 덤프 7개와 새로운 정기 백업 2,383,740,224바이트가 보존돼 있고, 남은 정리 후보는 0개였습니다. 자세한 프로세스·해시·정리 전후 감사 파일 근거는 [v108 기록](releases/20261010-v108/ACCEPTANCE.md)에서 확인합니다.

새 백업은 용량 검사 전에 이 정리를 시도하고 실행 상태를 `running`으로 기록합니다. 다음 날 예약 작업이 새 코드로 자동 기동되는지 관측하는 일은 이번 수동 실행·배포 검증과 별도로 남아 있습니다.

## 10월 10일 00:47 KST — 정기 외부 백업 설치와 첫 실행

v101 서버의 실제 `hannun-db-backup.service`를 한 번 실행해 `market-20261009T153042Z.dump` 2,372,779,444바이트와 완결 manifest를 생성했습니다. 아카이브 목록·SHA·mtime 검증을 통과했고 서비스 종료 코드는 0입니다. 새 덤프의 PostgreSQL 전체 복원은 이 작업에서 하지 않았습니다.

PC에는 `KoreaStocks-OffsiteBackup`을 매일 05:30 KST, 현재 로그인 사용자·제한 권한·네트워크 필요·중복 금지·최대 2시간으로 등록했습니다. `pythonw.exe`와 창 없는 SSH를 사용합니다. 00:47에 같은 예약 작업을 수동 시작했으며 전송은 진행 중입니다. 다음 날의 자동 기동 및 새 복사본의 전체 검증은 등록 성공과 구분합니다.

관리 폴더 32GiB·덤프 8GiB 상한, 전송 후 여유 10GiB를 검사하고 용량 초과 시 기존 자료를 삭제하지 않습니다. 기존 검증 외부 복구점은 유지합니다. 운영 지침은 `docs/PC_OFFSITE_BACKUP.md`, 등록 증거는 `work/operations-20261009-v94/pc-offsite-registration.json`입니다.

새 서버 백업 후 여유 공간은 29,910,716,416바이트이며 모니터는 `disk_warning`을 표시했습니다. 수집 중단 기준 90%에는 도달하지 않았습니다. 이전 77.92% 관측을 현재 상태처럼 쓰지 않습니다. 실제 기록은 `v101-post-backup-operations.json`입니다.

## 10월 10일 00:00 KST — 외부 복구점 연결

v100에 `offsite_only` v2 증거 계약을 배포하고 실제 PC 복구점의 검증 기록을 서버 모니터에 연결했습니다. 원본·암호화본의 전체 해시를 기존 복호화·격리 PostgreSQL 복원 기록과 재대조한 후 가져왔습니다. 이 작업에서 새 DB 복원 시험이나 덤프 업로드는 하지 않았습니다.

- 원본: `market-20261008T153201Z.dump`, 2,047,457,606바이트.
- SHA-256: `0022244d1f67ceb0147535da413eaf64fac49c42cc51652c5ce64e7d7bba92b8`.
- 실제 서버 모니터 관측: `2026-10-09T15:00:29.713195Z`, `verified`, `issues=[]`.
- 이 파일은 PC에만 있으며, 최신 서버 덤프 `market-20261008T192012Z.dump`의 복원 검증은 아닙니다.
- 검증 범위는 격리 DB의 복원·스키마·건수입니다. 현재 운영 DB와 내용 전체가 일치한다는 검증은 아닙니다.
- 7일 뒤 재확인, 30일 뒤 증거 갱신이 필요합니다. 기간이 지나도 마지막 복구본을 자동 삭제하지 않습니다.
- PC 파일 경로·계정·증거 원문·비밀정보는 공개 API에 넣지 않습니다.

`work/operations-20261009-v94/v100-recovery-import.json`에 실제 반영 결과를 보존합니다. 수동으로 boolean을 채워 정상으로 바꾼 결과가 아닙니다. `scripts/publish_offsite_recovery_receipt.py`가 실제 파일과 기존 검증 결과를 대조하고, 서버가 계약과 만료 시각을 확인합니다. 관련 53개 시험과 Ruff를 통과했습니다.

PC 외부 백업을 주기적으로 새로 만드는 예약 작업은 아직 설치·검증하지 않았습니다. 서버 정기 백업과 이번 외부 복구점 연결을 외부 백업 전체 자동화 완료로 표현하지 않습니다. 아래의 `backup_offsite_restore_unverified`는 v99 당시의 기록입니다.

## 23:23 KST 운영 반영

활성 v99의 운영 파일을 `/srv/services/korea-stocks/backups/config/ops-v99-20261009T142309Z`에 보존한 뒤 교체했습니다. 모니터 타이머는 교체 중 잠시 멈추고 복구했으며, 정기 백업 서비스가 실행 중이 아닌 것도 먼저 확인했습니다. 모니터를 실제 한 번 실행해 정적 웹과 v99 버전이 일치하고 `market-web_stopped`가 사라진 것을 확인했습니다. 두 기존 타이머는 활성 상태입니다.

이 검증에서 남은 유일한 issue는 `backup_offsite_restore_unverified`입니다. 아래에 설명한 PC 검증 덤프와 서버 최신 덤프가 다르므로, 검증한 범위를 넘겨 완료 표시하지 않았습니다. 파일 교체와 모니터 실행 자체는 데이터를 삭제하지 않습니다. 결과는 `work/operations-20261009-v94/v99-operational-deployment.json`입니다.

이후 앱 패치가 운영 수정을 누락하지 않도록 `deploy-market-backend-patch.py`와 추출 허용목록에도 세 파일을 추가했습니다. 배포 설정·비밀 파일·다른 운영 스크립트는 계속 제외하며 패키징·저장 형식 보호 시험 41개를 통과했습니다. 신규 배포는 기존처럼 로컬 검증한 promoter를 업로드하는 경로를 사용합니다.

## 실제 실행 중인 작업

후속 실행(23:38 KST): v91·v92·v94·v95·v96은 PC 외부 아카이브 전체 검증과 v91 실제 복원 후 해당 경로만 정리했습니다. 현재 v99, 최근 정상 v97·v98, Next 롤백용 v93, OpenAlgo의 v73은 보존했습니다. 최종 디스크 사용률은 77.92%, 여유 34,186,637,312바이트이며 API·수집기·OpenAlgo가 실행 중입니다. 23:31 적용한 경로 정렬 수정과 Windows 긴 경로 검증은 원본 manifest를 보존한 채 검증 일관성을 고친 것입니다. 전체 근거는 `docs/releases/20261009-v99/ACCEPTANCE.md`의 추가 공간 정리 항목과 `work/operations-20261009-2`에 있습니다.

점검 자료: `work/retention-audit-20261009/installed-schedules.json`, 관측 시각 `2026-10-09T14:00:08Z`. 당시 활성 백엔드는 `20261009-shared-write-v98`이었습니다. 이후 v99 승격은 부모 작업에서 수행했습니다.

| 작업 | 기존 실행 상태 | 이번 처리 |
|---|---|---|
| `hannun-db-backup.timer` | 활성, 매일 04:20 KST | 기존 타이머와 일별 7개·주별 4개 정책 유지 |
| `korea-stocks-monitor.timer` | 활성, 2분 간격 | 기존 `monitor.py`에 보존·백업 상태 검사 추가 |
| 과거 자료 용량 제한 | 수집 작업 내부에서 실행 | 기존 90% 중단·80% 미만 및 쓰기 검사 후 재개 유지 |
| 비활성 배포본 정리 | 수동 실행 | 자동 점검만 추가, 외부 검증 없는 자동 삭제 금지 |
| PC 외부 DB 백업 | 실행 도구 존재, 정기 실행 확인 안 됨 | 실제 외부 복원 증거와 서버 추적 상태를 구분 |

기존 모니터는 의도적으로 중지한 Next.js 컨테이너도 장애로 판단했습니다. 새 코드는 활성 정적 배포 마커, private/public manifest의 SHA-256과 릴리스, 실제 public current 포인터가 모두 일치할 때에만 `market-web=exited`를 정상으로 처리합니다. 다른 컨테이너의 중지는 계속 장애입니다.

## 변경 파일과 안전 조건

- `ops/market/monitor.py`: API 실패 중에도 호스트 디스크를 검사합니다. 배포본은 메타데이터만 읽고, 트리 전체 스캔·압축·삭제를 실행하지 않습니다. 같은 문제는 반복 출력하지 않습니다.
- `ops/market/retain_releases.py`: 현재, 최근 정상 배포 2개, 컨테이너 이미지·마운트, OpenAlgo 참조, 활성 정적 웹, 저장 형식 rollback floor를 보존합니다. 실패·미확인 배포본과 partial/symlink는 정리 대상에서 제외합니다.
- 같은 보존 도구의 실제 정리는 기존 `.deploy.lock` 아래에서 수행합니다. 후보별 전체 내용 해시, 외부 아카이브 해시, 아카이브의 모든 파일 검증 및 별도 배포본 복원 증거가 필요합니다. 크기가 같아도 내용이 바뀌면 전체 삭제를 중단합니다.
- `scripts/offsite_release_archive.py`: 현재 배포본의 보존 도구를 사용합니다. PC에서 모든 아카이브 파일과 하드링크의 해시를 확인하고, 배포본 하나를 실제 복원한 결과를 서버에 전달합니다. 기존 다운로드 중 partial을 덮어쓰지 않습니다.
- `ops/market/backup.py`: 다음 덤프 예상 공간(직전 크기의 2배, 최소 256MiB)과 디스크 10% 여유(최소 768MiB)를 먼저 확인합니다. 부족하면 기존 백업을 지우지 않고 `blocked_capacity`로 종료합니다. 이는 사전 검사이며 dump 도중의 무제한 증가를 보장하지 않습니다.
- 백업 완료 시 덤프별 SHA-256·크기·mtime·검증 기록을 별도 manifest에 보존합니다. 실패는 `failed`와 오류 클래스만 기록합니다. 빈 `.dump`는 일별·주별 보존 슬롯과 최신성 판정에서 제외하되 자동 삭제하지 않습니다. 진행 중 `.partial`과 배포 전 백업도 그대로 둡니다.

## 최신 백업과 마지막 검증 복구점

최신 정기 백업은 36시간 기준으로 판단합니다. 외부 복구점은 **그보다 오래된 검증본일 수 있습니다**. 새 정기 덤프가 생겼다는 이유로 과거의 유효한 복구 검증을 취소하지 않으며, PC가 꺼져 있어도 마지막 검증본을 유지합니다.

서버에서 추적하는 복구점은 다음 두 파일로 결합합니다.

`backups/<덤프이름>.manifest.json`:

```json
{
  "state": "completed",
  "source": "market-YYYYMMDDTHHMMSSZ.dump",
  "bytes": 123,
  "sha256": "실제 원본 덤프 SHA-256",
  "source_mtime_ns": 123,
  "archive_list_verified": true,
  "restore_verified": true
}
```

`backups/database-recovery-verified.json`:

```json
{
  "source": "market-YYYYMMDDTHHMMSSZ.dump",
  "sha256": "같은 실제 원본 덤프 SHA-256",
  "decryption_hash_verified": true,
  "restore_verified": true,
  "verified_at": "실제 검증 시각",
  "evidence": ["복호화 결과 경로", "격리 PostgreSQL 복원 결과 경로"]
}
```

이는 형식 예시입니다. 값을 사실로 확인한 운영자가 증거에서 가져와야 하며, boolean을 임의로 채워 넣으면 안 됩니다. 원격 파일의 실제 SHA·크기·mtime과 기존 복구 증거가 일치해야 합니다. 해시가 맞는 암호화 사본만으로 PostgreSQL 복원 통과를 대신하지 않습니다.

유효한 receipt가 가리키는 서버의 덤프 한 개는 일별·주별 보존과 별도로 보호합니다. receipt 자체가 없으면 기존 정상 보존 정책을 계속 사용합니다. 존재하지만 일치하지 않는 receipt는 잘못된 복구점 삭제를 막기 위해 정리를 차단하고 확인 대상으로 남깁니다.

### 현재 외부 복구 증거의 범위

부모 작업에서 확인한 PC 원본·암호화본과 격리 복원 증거는 `market-20261008T153201Z.dump`에 해당합니다. 크기는 2,047,457,606바이트, SHA-256은 `0022244d1f67ceb0147535da413eaf64fac49c42cc51652c5ce64e7d7bba92b8`입니다.

- 복호화: `work/backups/reports/backup-verification.json`
- PostgreSQL 복원: `work/tools/postgresql-restore/verification-20261009-retry/verification.json`

이 덤프는 현재 VPS에는 없고 PC에 보존되어 있습니다. VPS의 최신 덤프 `market-20261008T192012Z.dump`와는 다른 파일입니다. 따라서 위 검증을 최신 VPS 덤프의 복원 통과로 옮기거나 새 receipt로 가장하지 않습니다. 현 코드의 `backup_offsite_restore_unverified`는 **서버 파일과 결합해 추적 가능한 복구점이 아직 없다는 범위**입니다. PC에 실제 검증 복구본이 없다는 뜻은 아닙니다. 원본 재업로드·추가 전체 복원은 이번 변경에서 실행하지 않았습니다.

## 부모 작업의 적용 절차

1. 활성 `current`와 v99 릴리스를 대조합니다. 기존 타이머 설정은 그대로 유지합니다.
2. 활성 릴리스의 `ops/market/monitor.py`, `backup.py`, `retain_releases.py` 원본을 프로젝트 백업 경로에 보관합니다. 파일 해시를 기록한 뒤 세 파일을 원자 교체합니다. 비밀 파일은 복사하지 않습니다.
3. 호스트 Python으로 세 파일 `py_compile`을 실행합니다.
4. `python3 /srv/services/korea-stocks/current/ops/market/retain_releases.py inventory --metadata-only`를 실행합니다. 실제 `archive`·`apply`는 이 검증에 포함하지 않습니다.
5. `systemctl start korea-stocks-monitor.service` 후 `shared/monitor.json`을 읽습니다. 정적 웹/컨테이너 오탐은 없어야 하며 실제 백업·외부 복구 증거 부족은 별도 상태로 남깁니다. 새 서비스나 타이머를 만들 필요가 없습니다.
6. 장애가 있으면 세 파일만 이전 버전으로 되돌립니다. DB·볼륨·다른 프로젝트·앱 컨테이너를 건드리지 않습니다.

PC의 외부 배포 아카이브는 수정한 기존 `scripts/offsite_release_archive.py`를 사용합니다. 신규 날짜로 아카이브→전체 해시 검증→격리 복원 후 `--apply`를 실행할 수 있지만, 이 문서 작성 중에는 실행하지 않았습니다.

## 검증

```powershell
.venv/Scripts/python.exe -m pytest tests/test_release_retention.py tests/test_market_monitor.py tests/test_backup_retention.py -q -ra
```

2026-10-09 로컬 결과: **34개 통과, skip 없음**. 대상 7개 파일 Ruff 검사 통과. 검증 대상은 보호 참조, 같은 크기 내용 변경, 외부 검증 누락, pending 보존, 아카이브 하드링크 검증, 빈 덤프 슬롯, 오래된 검증 복구점 유지, 실패 기록, 정적 웹 판정, 디스크 80/90 경계입니다.

Windows의 symlink 생성 권한이 없는 테스트 환경에서는 포인터 경로 해석만 fixture로 대체합니다. 파일·내용 해시·보존 후보·시험용 삭제는 실제 임시 디렉터리에서 검증합니다. 실제 Linux 서비스 주기 실행의 수정 후 결과는 부모 배포 작업에서 별도로 기록해야 합니다.
