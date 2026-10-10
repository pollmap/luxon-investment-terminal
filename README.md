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

2026년 10월 10일 15:19 KST 공개 API·수집기·기존 주소와 Cloudflare 정적 화면은 **v122**입니다. [최신 변경·실제 검증 기록](docs/releases/20261010-active-refresh-v122/ACCEPTANCE.md)에서 구현·실제 자료·화면·공개 반영을 구분합니다.

JYP 투자자·대차·신용 103일과 공매도 거래 203일을 10월 8일까지 연결했습니다. 검색의 중복 DB 조회를 줄이고, 다섯 거래 자료가 각자의 시각으로 최신화를 판단하도록 수정했습니다. 모바일 기간 버튼은 글자가 갈라지지 않게 3열×2행으로 배치했습니다. 공개 자산/API 18개 검사, VPS390·Cloudflare1440의 검색→수급→대차·신용→공매도→목록 복귀와 해당 앱 커밋의 GitHub CI를 통과했습니다. 정상 worker의 삼성전자 5종 자료 재수신과 기존 과거 행 보존도 확인했습니다.

**전체 완료는 아닙니다.** VPS 여유 약 16.26GB(15:30 KST)에서 과거 수집은 `capacity_limited`입니다. 미사용 배포본 4개는 PC 보관·전체 파일 해시·복원 확인 후 정리했습니다. 중단된 17,014,175행 백업의 독립 복원은 실행 중이며 전체 복원·현재 운영 저장 구조 전환·원본 삭제는 아직 하지 않았습니다. 전체 과거 자료·404·TTM/분할·배당 근거·장중 실제 수신·실사용·보안 경고가 남습니다. 공개 첫 목록은 각 1회 관측에서 7.03초(VPS390)·4.73초(Cloudflare1440)였고, 폭과 조건이 달라 호스트 비교로 사용하지 않습니다. [최근 공개 100동시 측정](docs/releases/20261010-backfill-status-v118/ACCEPTANCE.md)의 검색300ms·상세500ms 목표 미달도 유지됩니다.

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
- [최신 공개 검증](docs/releases/20261010-active-refresh-v122/ACCEPTANCE.md) · [전체 저장 대조](docs/releases/20261010-v113/STORAGE_VALIDATION.md)
- [현재 앱 CI와 로컬 검사](docs/CI.md)
- [이전 터미널 README](docs/history/LEGACY_TERMINAL_README.md)

현재 실행 코드는 위 구조를 기준으로 봅니다. 이전 `services/api`, 연구 계산·터미널 코드는 재사용과 이력 확인을 위해 남겨 둔 별도 영역입니다. 과거 문서의 개인용·해외 시장·로그인·Vercel 설명은 현재 제품의 요구사항이 아닙니다.
