# 주식담

로그인 없이 국내 주식의 가격·수급·기업 실적을 살펴보는 PC·모바일 웹입니다. 시장 순위에서 종목을 찾고, 차트와 재무를 확인한 뒤 관심종목에 저장할 수 있습니다.

[공개 서비스](https://stocks.62-171-141-206.sslip.io/stocks) · [Cloudflare 화면](https://korea-stocks-edge.lch68-workers.workers.dev/stocks)

## 범위와 기능

- 코스피·코스닥 보통주와 우선주. 로그인 없이 조회하며 관심종목·설정은 브라우저에 보관합니다.
- 검색·시가총액/거래대금 순위·업종/섹터·관심종목·2~4종목 비교.
- 선·영역·캔들·OHLC 차트, 일별 가격, 투자자·프로그램·공매도·대차·신용 자료.
- 실적과 주가 비교, EPS·현금흐름·EBITDA 등 확보된 지표, 재무·배당·주주·공시·뉴스.
- 주문·계좌·코넥스·ETF/ETN·해외 종목·자체 투자점수·배당 재투자는 제외합니다.

증권사 연결은 **토스 하나**입니다. DART·XBRL·KIND·기업 IR과 검증된 공개 자료로 기업 정보를 보완합니다. 원천에서 제공하지 않거나 아직 검증하지 못한 값은 만들어 채우지 않습니다.

## 현재 상태

2026년 10월 10일 16:50 KST 공개 API·수집기·기존 주소와 Cloudflare 정적 화면은 **v125**입니다. [최신 변경·검증 기록](docs/releases/20261010-index-reuse-v125/ACCEPTANCE.md)에서 구현·실제 자료·화면·공개 반영을 구분합니다. 검색 색인이 이미 읽은 종목 목록을 다시 읽지 않게 했고, 정적 화면에서 공개 API 연결을 미리 준비합니다. 뉴스 로딩과 목록 표시를 분리한 v124 수정도 유지합니다. 양쪽 공개 주소의 자산·API 20개와 2,765종목 검색 색인, 정적 화면 회귀 및 GitHub CI를 확인했습니다.

JYP 신용융자·신용대주·대차 103일, 공매도 거래 203일의 저장 자료 연결과 삼성전자 5종 최신 자료 재수신은 v121·v122 증거입니다. 실제 비교 날짜가 없는 TTM의 자동 선택은 v124에서 수정했습니다. 현재 기아의 정상 TTM 2개·연간 비교값 25개가 유지된 것을 당시 실제 응답으로 확인했습니다.

[전체 대상 집계](docs/releases/20261010-analysis-window-v124/DATA_STATUS.md)는 16:35 KST 기준 2,765종목입니다. 일봉 514종목·투자자 수급 344종목 등에 마지막 페이지 도달 기록이 있으며, 자료별 진행도가 다릅니다. ‘자료 있음’을 전체 기간 완료로 세지 않습니다.

**전체 완료는 아닙니다.** VPS 여유는 약 15.51GB이며 과거 수집은 용량 제한 중입니다. 17,014,175행 백업의 독립 복원은 15,282,400행에서 PC 디스크 증가 한도에 도달해 종료됐습니다. 원본 해시는 다시 대조했고 부분 결과를 보존했지만 전체 복원·최신 변경분 대조·운영 저장 구조 전환·원본 삭제는 완료하지 않았습니다. 전 종목 과거 자료·TTM/분할·배당 근거·장중 실제 수신·실사용 확인도 남습니다. 이번에 성능 목표를 재측정하지 않았으며 [최근 공개 100동시 측정](docs/releases/20261010-backfill-status-v118/ACCEPTANCE.md)의 검색300ms·상세500ms 목표 미달은 유지됩니다.

## 구조

```text
apps/web/                 Next.js·React·TypeScript, /stocks
services/market_dashboard/ FastAPI, 공유 수집기, 데이터·계산
ops/cloudflare/           정적 파일·공개 조회 전달
ops/market/               VPS 배포·백업·복구·보관 관리
ops/openalgo/             내부 조회 연동과 공개 소스 제공
scripts/                  빌드·배포·검증 도구
tests/                    계산·조회·저장·운영 회귀
docs/                     계약·기획·배포 근거
```

Cloudflare는 정적 화면을 제공하고 VPS에는 API·PostgreSQL·Redis·수집·실시간 연결을 둡니다. 로컬에서는 SQLite도 지원합니다. 원본·정정 이력과 회사/종목 식별을 보존하고, 검증한 기간·단위·주식 기준으로 계산합니다.

## 로컬 실행

Python 3.12 이상, Node.js 24, pnpm 10.29.3을 사용합니다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install ".[dev,market]"
pnpm install --frozen-lockfile
pnpm build
./scripts/start-market.ps1
```

별도 API만 실행하려면:

```powershell
.venv/Scripts/python.exe -m uvicorn services.market_dashboard.app:app --host 127.0.0.1 --port 8210
```

실제 수집 설정은 [환경변수 템플릿](ops/market/.env.example)과 [실행·운영 안내](README-MARKET.md)를 따릅니다. 인증정보와 운영 DB는 저장소에 포함하지 않습니다. 새 복제본에는 실제 시장 자료가 없으며, 비밀키 없이 전체 자료가 자동 생성되지는 않습니다.

## 개발·검증

- [API와 옵션 계약](docs/toss-api-contract.md)
- [출처 공유 저장](docs/SOURCE_TRACE_STORAGE.md) · [거래 자료 저장](docs/TRADING_PROJECTION_STORAGE.md)
- [외부 백업·복구](docs/PC_OFFSITE_BACKUP.md)
- [최신 공개 검증](docs/releases/20261010-index-reuse-v125/ACCEPTANCE.md) · [전체 저장 대조](docs/releases/20261010-v113/STORAGE_VALIDATION.md)
- [2,765종목의 실제 수집 상태](docs/releases/20261010-analysis-window-v124/DATA_STATUS.md) · [독립 복원·변경 자료 적용 범위](docs/releases/20261010-analysis-window-v124/LOCAL_RECOVERY.md)
- [현재 앱 CI와 로컬 검사](docs/CI.md)
- [이전 터미널 README](docs/history/LEGACY_TERMINAL_README.md)

현재 실행 코드는 위 구조를 기준으로 봅니다. 이전 `services/api`, 연구 계산·터미널 코드는 재사용과 이력 확인을 위해 남겨 둔 별도 영역입니다. 과거 문서의 개인용·해외 시장·로그인·Vercel 설명은 현재 제품의 요구사항이 아닙니다.
