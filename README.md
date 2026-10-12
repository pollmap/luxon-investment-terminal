# 주식 장보기

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

2026년 10월 13일 통합 중인 **v134**는 실적·주가와 실제 배수 흐름 전환, 실제 회계기간 증감률, 이익·현금흐름의 근거 계약을 추가합니다. REA 실행과 공개 기능 대조는 [변경·검증 기록](docs/releases/20261012-v134/ACCEPTANCE.md)에 정리했습니다. 후보 검사와 실제 공개 반영은 별도 기록하며, 통합 시작 시 공개 API·수집기는 v133, 정적 화면은 v132였습니다. 최초 시세 차트는 달력 기준 최근 3개월의 일봉 캔들입니다. 기존 공유 링크·관심·선택을 보존하며 회사·가격 요약과 가격 차트는 자료 영역의 조회 실패와 분리합니다.

JYP Ent.(035900)의 보존 원문 22개와 기존 사실 16개를 대조하고 EPS 입력 사실 12개를 추가했습니다. 연결 TTM EPS 2구간을 계산했으며 주식 단위 검토창은 2025-01-01~2026-10-08입니다. 두 공개 주소에서 EPS × 15배 비교가 통과했고 공개 전 날짜에는 비교값이 없는 것을 확인했습니다. 실제 공개 브라우저에서도 10월 8일 기준 EPS 3,278.72원·비교값 49,181원을 확인했습니다. 이것은 해당 기업의 검증된 구간이며 전 종목 TTM 완료 증거가 아닙니다.

[새 전체 대상 집계](docs/releases/20261011-v129/current-coverage.json)는 10월 11일 02:07 KST 기준 2,765종목입니다. 조정 일봉 526종목·투자자 수급 356종목 등에 원천 마지막 페이지 영수증이 있습니다. 자료별 진행도가 다르며 ‘자료 있음’, 작업 커서, 시도 횟수를 전체 기간 검증 완료로 세지 않습니다. [토스 상태 검사](docs/releases/20261011-v129/toss-runtime.json)에서는 대상 GET 24개 모두 저장된 성공 응답·원본 해시가 있었지만, 전체 옵션·단위·화면 값의 검증을 뜻하지 않습니다. TTM·분할·배당 근거는 부분 확보 상태입니다.

근거 자료 검색은 공개 API와 화면에 연결했습니다. 현재 운영에서는 저장된 문서 제목의 키워드 검색만 제공합니다. 원문 청킹·관계·벡터 결합·384차원 E5 INT8 내부 작업자는 구현하고 로컬 추론을 검사했지만, 운영 본문 색인과 한국어 50문항 품질 검증은 아직입니다. 제목 검색을 의미 검색으로 표시하지 않습니다.

**전체 완료는 아닙니다.** 디스크는 여전히 약 93%입니다. 비활성 배포 5개를 외부 사본의 파일 해시·복원으로 대조한 뒤 약 1.15GB를 정리했습니다. 독립 DB 복원은 디스크 증가 예산 초과로 전체 검증에 실패했으므로 원본과 부분 결과를 보존했습니다. 최신 계획에 맞춰 90%에서 대량 작업 중단·80% 미만과 쓰기 검사 통과 후 재개 정책을 적용했습니다. 운영 DB 전환·원본 삭제·대량 색인 확대는 하지 않았습니다.

공개 인터넷 독립 TLS 연결 100개로 각 시험 2,000요청을 측정했습니다. 해당 서비스의 Nginx 캐시 잠금 개선 후 v128의 후속 배치 p95는 VPS 검색 **516ms·상세 502ms**, Cloudflare 검색 **2,590ms·상세 1,934ms**였습니다. 검색 300ms·상세 500ms 목표에 미달하여 VPS 동적 조회와 Cloudflare 정적 화면을 유지합니다. 실제 장중 체결·호가·복구, 전 종목 자료·TTM/분할·배당, 전체 백업 복원과 실제 사용자 평가는 남습니다.

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
- [최신 공개 검증](docs/releases/20261011-v130/ACCEPTANCE.md) · [전체 저장 대조](docs/releases/20261010-v113/STORAGE_VALIDATION.md)
- [2,765종목의 실제 수집 상태](docs/releases/20261011-v129/current-coverage.json) · [독립 복원·변경 자료 적용 범위](docs/releases/20261010-analysis-window-v124/LOCAL_RECOVERY.md)
- [근거 문서 검색 계약](docs/research-search-20261011.md)
- [현재 앱 CI와 로컬 검사](docs/CI.md)
- [이전 터미널 README](docs/history/LEGACY_TERMINAL_README.md)

현재 실행 코드는 위 구조를 기준으로 봅니다. 이전 `services/api`, 연구 계산·터미널 코드는 재사용과 이력 확인을 위해 남겨 둔 별도 영역입니다. 과거 문서의 개인용·해외 시장·로그인·Vercel 설명은 현재 제품의 요구사항이 아닙니다.
