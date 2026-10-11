# 국내증시 백엔드 배포

2026-10-05 기준 공개 서비스는 `/srv/services/korea-stocks`에서 실행합니다. 과거 `scripts/deploy-market.py`는 폐기된 `/srv/hannun` 구성에 연결되어 실행을 막았습니다.

백엔드 변경은 로컬에서 소스를 묶고 SSH로 전송한 뒤 다음 형식으로 배포합니다.

```powershell
.venv\Scripts\python.exe scripts/deploy-market-backend-patch.py `
  --release 20261005-example-v75 `
  --expected 20261005-provider-catalogue-404-v74
```

`--expected`는 활성 릴리스와 정확히 일치해야 합니다. 실행기는 현재 링크, 아카이브 SHA-256, SSH 호스트 키를 확인한 뒤 새 릴리스를 만들고 API·수집 작업자만 교체합니다. API 검색·상세·웹 응답 검사가 통과해야 `current`를 바꿉니다. 실패하면 기존 환경값과 API·작업자를 복구합니다.

DB·Redis·웹·OpenAlgo의 컨테이너 ID가 변하면 검증 실패로 처리합니다. 서비스 데이터와 비밀 설정을 배포 아카이브에 넣지 않습니다. 로컬 패키지와 결과는 `work/releases/<release>/`에 남습니다.

배포 권한과 SSH 사용은 이미 승인되어 있습니다. 별도 키 입력을 받거나 공개 화면에 비밀값을 출력하지 않습니다. 전체 화면 전환·데이터베이스 스키마 변경에는 이 작은 백엔드 전용 배포기를 재사용하지 않고 별도 검증을 거칩니다.
