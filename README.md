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

2026년 10월 14일 확인 기준, **API·공유 작업자·VPS 정적 화면·Cloudflare v140**입니다. [v140 변경·검증 기록](docs/releases/20261013-v140/ACCEPTANCE.md)에서 실행 소스·CI·자산 해시와 남은 조건을 확인할 수 있습니다. 제품 소스는 `6e7b6ce`이며 문서 커밋과 구분합니다.

검색은 일치 후보만 순위를 읽고 최대 30개 종목에 화면 필드를 결합합니다. 상세 요약은 전체 회사 과거 재무 JSON을 읽는 경로와 분리했습니다. 최초 시세는 달력 기준 3개월 일봉 캔들이며, 기존 공유 링크·종목별 선택·관심종목을 우선 보존합니다. 기업 분석은 실적과 주가, 확보된 EPS·OCF·EBITDA와 실제 회계기간의 실적 변화를 제공합니다. 검증된 일부 구간을 전체 5년 실적 검증으로 표시하지 않습니다.

Python 2,896개·브라우저 50개와 타입·정적 자산/PWA 검사가 통과했습니다. 두 공개 주소에서 같은 v140 manifest·대표 JS·API 버전과 선택 내부/주문 경로의 404를 대조했습니다. 실제 PC 검색→종목→시세·수급 표시도 확인했습니다. 실제 휴대폰과 아버님 사용 평가, 실제 장중 수신은 별도입니다.

비활성 배포본 8개는 PC 외부 보관·14,878개 파일 해시·배포본 하나의 독립 복원을 검증한 뒤 정리했습니다. 파일 논리 합계는 약 1.79GB이며 물리 회수량과 구분합니다. 금융 원본·DB·미검증 복원은 보존했습니다. 배포 후 VPS 가용은 약 3.56GiB이며 대량 수집 재개 기준 80% 미만까지 약 25.29GiB가 더 필요합니다. PC 캐시 삭제는 자동 승인 정책이 거절해 실행하지 않았습니다.

**전체 완료는 아닙니다.** 개선한 독립 복원은 10월14일00:00 KST에 최소 여유4GiB 기준으로 중단됐으며, replay 검증4,226,800행은 전체17,014,175행·8,071참조·정렬 해시·원본 참조 통과가 아닙니다. 소유 PG 종료 후 공간은 회복했지만 일시 할당 원인은 미확정이고 같은 조건으로 자동 반복하지 않습니다. dated archive 복원 이후에도 현재 운영 변경분은 별도 검증해야 합니다. 전 종목 과거 자료·새 기업 TTM/분할/배당 확대는 공간 제한 상태이고, 094800의 토스 404를 종목 제외로 처리하지 않았습니다. 주식 단위 스냅샷 5개는 전 종목 TTM 완료 수가 아닙니다.

복원 종료 후 공개 인터넷 100독립연결/1,000요청에서 v139→v140 재사용 p95는 검색1.83→4.22초로 회귀, 상세1.48→0.60초로 개선했습니다. 목표300ms·500ms는 모두 미달이며 검색 회귀를 조사 중입니다. projection 모의 계측 감소를 서비스 전체 속도 개선으로 환산하지 않습니다. 정상 상태의 문서 검색은 저장 제목의 키워드 검색이며, 본문·벡터 색인과 한국어 품질 검증은 아직입니다. braces 보안 경고의 공식 수정 버전도 없어 미해결 상태를 유지합니다.

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
- [최신 공개 검증](docs/releases/20261013-v140/ACCEPTANCE.md) · [전체 저장 대조](docs/releases/20261010-v113/STORAGE_VALIDATION.md)
- [10월 13일 2,761종목의 가격 메타 집계](docs/releases/20261013-v136/PRICE_COVERAGE.md) · [독립 복원·변경 자료 적용 범위](docs/releases/20261010-analysis-window-v124/LOCAL_RECOVERY.md)
- [근거 문서 검색 계약](docs/research-search-20261011.md)
- [현재 앱 CI와 로컬 검사](docs/CI.md)
- [이전 터미널 README](docs/history/LEGACY_TERMINAL_README.md)

현재 실행 코드는 위 구조를 기준으로 봅니다. 이전 `services/api`, 연구 계산·터미널 코드는 재사용과 이력 확인을 위해 남겨 둔 별도 영역입니다. 과거 문서의 개인용·해외 시장·로그인·Vercel 설명은 현재 제품의 요구사항이 아닙니다.
