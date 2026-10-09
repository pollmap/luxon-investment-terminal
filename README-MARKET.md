# 주식담

토스 단일 증권사 연결 · OpenDART 기업 자료 · 로그인 없는 PC·모바일 주식 조회 서비스입니다. 코스피·코스닥 보통주·우선주를 대상으로 합니다. 코넥스·ETF·ETN·해외 종목·주문·계좌 연결·배당 재투자는 개발 범위에서 제외합니다.

공개 화면: https://stocks.62-171-141-206.sslip.io/stocks

같은 정적 화면의 Cloudflare 주소: https://korea-stocks-edge.lch68-workers.workers.dev/stocks

현재 서비스의 구현·연결·공개 검증은 이 문서와 [v110 변경·검증 기록](docs/releases/20261010-v110/ACCEPTANCE.md)을 기준으로 확인합니다. API·수집기는 v110이며 화면은 변경 없는 v102 산출물을 사용합니다. Cloudflare Worker의 캐시 계약은 Origin별로 분리한 `public-read-v3-origin`입니다. 전체 데이터 확보와 공개 성능 목표는 아직 완료되지 않았습니다. [이전 LUXON README](docs/history/LEGACY_TERMINAL_README.md)는 역사 기록으로 분리했습니다.

## 연결과 기능

- 토스: 공개 시장 조회, 섹터, OAuth, 국내 체결·호가 공유 스트림. 기능·옵션별 실제 연결은 [API 계약](docs/toss-api-contract.md)과 버전별 검증 기록으로 확인하며, 계좌·주문 API는 허용 목록에 없습니다.
- OpenDART: 기업 식별·재무·공시·XBRL EPS 입력·배당 결정 원문.
- KIND·기업 IR·검증된 공개 수집기: 상장 목록, 거래원·테마·기업 자료 보완. 원래 출처를 유지합니다.
- 검색·순위·관심·비교·선/영역/캔들/OHLC·기간별 수급·프로그램·공매도·신용·대차·재무·주주·배당·예상실적 화면을 유지합니다.
- 토스 기간별 6종 순위, 시장 투자자 매매, 국채 6종·환율·거래 일정, 종목 유의사항·상하한가를 추가했습니다.

키움·한투 인증·수집·대체 호출은 제거했습니다. 이전 개발 기록은 [별도 문서](docs/market-history-before-toss-single-20261003.md)에 보존하며 현재 실행 지침이 아닙니다.

## 실행

```powershell
.\scripts\start-market.ps1
```

새 환경은 `ops/market/requirements.txt`와 `pnpm-lock.yaml`을 사용합니다. 서버만 실행하려면:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.market_dashboard.app:app --host 127.0.0.1 --port 8210
```

설정은 `ops/market/.env.example`을 참고하세요. `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `DART_API_KEY`는 서버 수집기에서만 사용합니다. 비어 있는 환경변수는 기존 비밀 파일을 가리지 않습니다. `TOSS_CREDENTIAL_FILE`로 비밀 파일 위치를 지정할 수 있습니다. 인증 토큰은 `TOSS_TOKEN_FILE`에 보관하며 웹 요청마다 발급하지 않습니다.

## 저장·배포

- SQLite / PostgreSQL, Redis 스냅샷, 정규화 `market_time_series`, XBRL 사실과 정정 이력.
- 원본 바이트는 해시별 보존합니다. 기존 JSON은 읽기 호환 계층이며 과거 이력은 다음 갱신 때 점진적으로 정규화됩니다.
- VPS `/srv/services/korea-stocks/current`, 별도 `releases`, `shared/secrets`, `backups`; 기존 공개 주소 유지.
- 화면은 `scripts/build-market-static.mjs`로 정적 빌드하고 `ops/market/promote_static_web.py`로 기존 주소에 반영합니다. Cloudflare도 같은 산출물을 사용합니다. API·수집·PostgreSQL·Redis·OpenAlgo는 VPS에 남기고 웹용 Next.js 서버는 중지했습니다.
- 백엔드 배포는 `scripts/deploy-market-backend-patch.py`에서 예상 이전 버전과 압축 해시를 검사합니다. 앱 롤백으로 DB를 과거 상태로 되돌리지 않습니다.
- [PC 외부 백업](docs/PC_OFFSITE_BACKUP.md)은 전송·암호화 검증과 PostgreSQL 복원 검증을 구분합니다.
- 삭제된 실행 모듈은 `ops/market/retired-files.json`으로 새 이미지에서도 제거합니다.
- DB 출처 정리는 `python -m services.market_dashboard.retire_providers`로 먼저 dry-run합니다. 적용은 검증된 백업 SHA256이 필요하며 수정 전 값을 별도 감사 행으로 보존합니다.

## 계산과 완료 범위

TTM EPS는 보통주 귀속이익, 기간 가중평균 주식 수, 공시 EPS 대조를 사용합니다. 분할 기준이 확인되지 않으면 비교선을 내보내지 않습니다. 배당 금액·기준일·배당락일·지급일은 각각의 근거를 연결하며 확인되지 않은 날짜를 다른 날짜로 채우지 않습니다. 배당 재투자·총수익률 계산은 현재 범위에서 제외합니다.

기능 구현, 실제 자료, 화면 검사, 공개 반영을 구분합니다. 수집 큐가 실행 중이라는 사실은 전 종목 전체 기간의 수집 완료가 아닙니다. 휴장일의 스트림 구독 승인은 장중 체결 검증이 아닙니다.

[API 계약](docs/toss-api-contract.md) · [구현·검증·남은 조건](docs/toss-single-implementation-20261003.md)
