# PC 외부 백업 실행

기준일: 2026-10-10 KST. VPS 및 예약 작업 설치는 운영자가 별도로 실행합니다.

## 현재 구조와 보존 조건

`ops/market/offsite_backup.py`를 일일 전송의 단일 진입점으로 사용합니다. 기존
`work/backups/backup_crypto.py`의 실제 DPAPI·AES-GCM 검증 코드를
`ops/market/backup_crypto.py`로 승격했습니다. 예전 도구와 KSTOCKS1 암호화 파일은
보존하며 자동 변환하거나 삭제하지 않습니다. 기존 LUXON Local Backup 스케줄은
로컬 Docker용으로, 이번 VPS 외부 백업과 다릅니다.

일일 실행은 다음을 수행합니다.

1. VPS의 가장 최근 비어 있지 않은 `market-YYYYMMDDTHHMMSSZ.dump`를 찾습니다.
2. 완결 manifest의 크기·mtime·SHA·아카이브 목록 검사와 실제 파일 헤더를 대조합니다.
   최신 dump의 manifest가 아직 없으면 이전 것을 최신처럼 보고하지 않고 실패합니다.
3. 고정된 SSH 호스트에서 읽기 전용으로 전송하며, 원격에서도 바이트 수를 제한하고
   전송 끝에 SHA와 파일 변경 여부를 검사합니다.
4. PC에서 SHA를 다시 검사하고 원본을 보존합니다. 독립 AES-256-GCM 키를 현재
   Windows 사용자 DPAPI로 보호한 뒤 전체 인증 복호화·암호문 SHA를 확인합니다.
5. 모든 검사가 끝난 폴더만 같은 디스크 안에서 원자적으로 완료 이름으로 바꿉니다.

**일일 작업은 PostgreSQL을 실행하거나 전체 복원하지 않습니다.** 완료 상태는
`transferred_encrypted_verified`, `postgres_restore_verified: false`입니다.
기존 `database-recovery-verified.json` 영수증이나 복원된 DB를 변경하지 않습니다.
새 백업을 복원 검증된 복구점으로 승격하려면 별도 `verify_postgres_restore.py`
검사와 `publish_offsite_recovery_receipt.py`의 정확한 SHA 연결이 필요합니다.

저장 위치는 `work/backups/daily/<dump 이름>/`이며 원본 dump, 암호문, `key.dpapi`,
암호화 manifest, `backup-verification.json`, `transfer.json`이 들어갑니다.
원본을 숨겨 암호문만 존재하는 것처럼 설명하지 않습니다. 해당 PC 사용자와
관리자의 파일 접근 권한을 그대로 사용합니다. DPAPI 키는 다른 PC·사용자로
자동 이동되지 않으며, 기존 복원 검증 원본을 삭제하지 않습니다.

상한은 관리 폴더 **32GiB**, 개별 dump **8GiB**, 다운로드 후 남길 여유 공간
**10GiB**입니다. 원본과 암호문 두 벌 및 메타데이터 공간을 전송 전에 예약해
검사합니다. 다른 프로세스의 디스크 사용까지 예약하는 OS 보장은 아닙니다.
작업 중 공간 부족 역시 실패로 남기고 이전 복사본을 보존합니다.

완료본·실패한 partial 모두 상한 계산에 포함합니다. 자동 삭제는 없습니다.
상한 도달은 `blocked_capacity`이며, 최신 백업 전송이 멈췄음을 숨기지 않습니다.
복원 검증 이후 운영자가 보존 대상을 정리하거나 다른 저장소를 지정해야 합니다.
무한히 쌓는 정책이나 검증 없이 오래된 원본을 지우는 정책은 사용하지 않습니다.

`last-attempt.json`은 실행 중·실패·공간 부족·전송 성공을 기록하고,
`last-transfer.json`은 마지막 전송 성공을 유지합니다. 같은 dump를 다시 실행하면
파일·암호문·DPAPI 인증을 확인하고 다운로드를 생략합니다. 실패한 partial은
복구 근거를 위해 보존하며 다음 시도에서 덮어쓰지 않습니다.

## 사전 확인과 수동 1회 실행

저장소 루트에서 실행합니다. 암호화 의존성은 이 PC의 기존
`work/tools/backup-crypto` 런타임을 사용합니다. 없으면 전송 전에 실패합니다.

```powershell
.venv/Scripts/python.exe -c "from ops.market.offsite_backup import codec; print(codec().MAGIC.decode())"
.venv/Scripts/python.exe ops/market/offsite_backup.py --latest
```

SSH는 기존 전용 키와 알려진 호스트 키를 사용합니다. 비대화형 접속 실패나
알 수 없는 호스트 키는 자동 승인하지 않습니다. 메타데이터 조회 제한은 45초,
다운로드는 최대 90분입니다. 로그에는 예외 클래스만 남기고 명령·키·DB 내용을
출력하지 않습니다.

## 예약 작업 등록 — 부모/운영자 실행

다음 코드는 설치 명령이며 문서를 작성하는 과정에서는 실행하지 않았습니다.
기존 작업이 있으면 중복 설치하지 않고 중단합니다. 작업 이름·현재 사용자·
동일 실행 경로를 확인한 뒤 수정 여부를 판단해야 합니다.

VPS 일일 백업 04:20 KST 이후 **05:30 KST**에 실행합니다. 현재 Windows 시간이
한국 표준시인지 검사합니다. 로그인한 현재 사용자, 제한된 권한, 네트워크 연결,
한 번에 하나, 최대 2시간으로 제한합니다. PC가 꺼져 있으면 다시 사용 가능할 때
실행하며, PC를 깨우거나 관리자 권한을 요구하지 않습니다.

```powershell
$stockRepo = (Resolve-Path '.').Path
$stockPython = (Resolve-Path '.venv/Scripts/pythonw.exe').Path
$stockScript = (Resolve-Path 'ops/market/offsite_backup.py').Path
$stockTaskName = 'KoreaStocks-OffsiteBackup'
if ((Get-TimeZone).Id -ne 'Korea Standard Time') { throw 'Expected Korea Standard Time' }
if (Get-ScheduledTask -TaskName $stockTaskName -ErrorAction SilentlyContinue) {
  throw 'Existing task found; inspect it before changing registration'
}
$stockAction = New-ScheduledTaskAction -Execute $stockPython `
  -Argument ('"{0}" --latest' -f $stockScript) -WorkingDirectory $stockRepo
$stockTrigger = New-ScheduledTaskTrigger -Daily -At '05:30'
$stockSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$stockUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$stockPrincipal = New-ScheduledTaskPrincipal -UserId $stockUser `
  -LogonType Interactive -RunLevel Limited
$stockTask = New-ScheduledTask -Action $stockAction -Trigger $stockTrigger `
  -Settings $stockSettings -Principal $stockPrincipal `
  -Description 'Korean stock VPS backup transfer, SHA and DPAPI verification; no automatic deletion or database restore.'
Register-ScheduledTask -TaskName $stockTaskName -InputObject $stockTask | Out-Null
Get-ScheduledTask -TaskName $stockTaskName | Get-ScheduledTaskInfo
```

예약 실행은 `pythonw.exe`를 사용해 콘솔 창을 열지 않습니다. SSH 자식 프로세스도
`CREATE_NO_WINDOW`로 실행하며 표준 입력은 닫습니다. 표준 출력·오류 스트림이
없는 환경에서도 `last-attempt.json`, `last-transfer.json`과 작업 종료 코드를
확인할 수 있습니다. 수동 점검은 앞의 `python.exe` 명령을 그대로 사용합니다.

## 검증 범위

회귀 검사는 작은 `fixture_non_production` 자료와 실제 현재 사용자 DPAPI를
사용합니다. 최신 완결본 선택, 헤더·SHA·mtime 불일치, 과거 자료 보존,
원자적 완료, 암호화·복호화, 중복 실행, 네트워크 실패, 공간 상한,
Windows 긴 경로와 안전한 오류 기록을 검사합니다.

예약 작업 등록, 실제 최신 dump 다운로드, 다음 날 예약 실행은 각각 운영 검증
단계입니다. 코드 테스트 통과를 해당 운영 결과로 기록하지 않습니다.
