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

2026년 10월 10일 공개 API·수집기는 **v110**, 정적 화면은 **v102**입니다. [변경·실제 검증 기록](docs/releases/20261010-v110/ACCEPTANCE.md)에서 구현, 실제 자료, 화면, 공개 반영을 구분합니다.

대상 2,765종목 중 원천 마지막 페이지까지 확인한 일봉은 602종목, 투자자 수급은 344종목입니다. 이는 수집 작업 기록 기준이며 모든 데이터가 완료된 종목 수가 아닙니다. 디스크 보호로 과거 수집은 중단 상태이고 최신 갱신은 실행 중입니다. 전체 과거 자료·TTM/분할·배당 근거·실제 장중 수신·공개 성능 목표·실사용 평가가 남아 있습니다.

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
- [최신 공개 검증](docs/releases/20261010-v110/ACCEPTANCE.md)
- [현재 앱 CI와 로컬 검사](docs/CI.md)
- [이전 터미널 README](docs/history/LEGACY_TERMINAL_README.md)

현재 실행 코드는 위 구조를 기준으로 봅니다. 이전 `services/api`, 연구 계산·터미널 코드는 재사용과 이력 확인을 위해 남겨 둔 별도 영역입니다. 과거 문서의 개인용·해외 시장·로그인·Vercel 설명은 현재 제품의 요구사항이 아닙니다.
