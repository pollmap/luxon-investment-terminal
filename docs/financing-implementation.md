# 대차·신용과 실시간 수신 보강 — 2026-09-26

## 구현과 사용자 흐름

`/stocks?code=005930` → 수급 → 대차·신용에서 대차잔고·신용융자·신용대주를 선택합니다. 기존 모바일 상위 네 메뉴는 유지했습니다. 잔고·신규·상환 수량, 신용 잔고비율, 1개월·3개월·1년, 터치 날짜 탐색, 펼치는 일별 표와 설명 패널을 제공합니다. 신규·상환은 일별 수량이며 기간 누적이나 잔고로 혼용하지 않습니다. 수량 단위는 주입니다. 원천 금액 단위의 운영 응답 검증 전에는 금액을 추가하지 않았습니다.

기존 수급 화면 캡처와 [FAST Graphs 공개 제품 설명](https://www.fastgraphs.com/)의 지표 선택·날짜별 값 확인 원리를 검토했습니다. 공개 이미지 주소는 도구에서 가져오지 못했으므로 같은 화면 크기의 외부 시각 비교를 완료했다고 주장하지 않습니다. 이번 변경은 기존 서비스의 표·설명·터치 차트·색상 토큰을 재사용합니다. 새로운 시각 디자인을 도입하지 않았습니다.

휴대폰에서 필요한 화면을 열 때만 컴포넌트와 데이터를 불러옵니다. 설명은 클릭·키보드로 열고 Escape·뒤로 가기로 닫습니다. 닫으면 원래 버튼으로 초점을 돌립니다. 조회 요청은 종목·기간 변경 또는 화면 종료 시 취소하고 숨겨진 탭에서는 폴링을 멈춥니다. 로딩·미제공·빈 결과·연결 오류·부분 이력·이전 정상 자료를 구분하며 출처 상세는 내부에만 보존합니다.

## 데이터 계약

| 구분 | 공식 API | 날짜·필드 |
|---|---|---|
| 대차 | `daily-loan-trans`, `HHPST074500C0` | 종목 구분 `3`, `bsop_date`, `rmnd_stcn`, `new_stcn`, `rdmp_stcn` |
| 신용융자 | `daily-credit-balance`, `FHPST04760000` | `stlm_date` 결제일, `deal_date` 매매일, `whol_loan_*` |
| 신용대주 | 같은 신용 응답 | `whol_stln_*`; 융자·전체 대차·공매도와 별도 저장 |

공식 계약: [한투 대차 조회](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_loan_trans/daily_loan_trans.py), [대차 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_loan_trans/chk_daily_loan_trans.py), [신용 조회](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_credit_balance/daily_credit_balance.py), [신용 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_credit_balance/chk_daily_credit_balance.py).

요청 API는 `GET /api/market/v1/securities/{code}/financing?period=quarter`입니다. 등록 종목과 기간을 검증하고 저장된 자료만 반환합니다. 실제 수집은 독립 큐에서 활성 종목부터 실행합니다. 날짜 커서로 최대 14페이지를 조회하며 커서 무시·중복 충돌·잘못된 날짜/숫자·음수·100% 초과 비율을 검출합니다. 실패한 페이지 앞에서 확보한 자료는 partial로 보존합니다. 빈 응답과 실패는 이전 정상 값을 지우지 않습니다.

한투 인증과 속도 제한은 기존 수집기와 공유합니다. 키가 없을 때는 unavailable을 반환하고 전체 종목의 불필요한 인증 요청을 만들지 않습니다. 원본은 기존 해시 아카이브에 보존합니다. 과거 차트·공매도·대차신용 큐는 변경 여부를 확인한 종목코드 캐시를 공유하여 큰 종목 JSON을 매 주기 재해석하지 않습니다. 저용량 디스크에서는 배경 수집을 중단합니다.

## 실시간 수신 보강

키움 스트림은 로그인과 종목 구독 성공을 구분합니다. 구독 거절을 ready로 표시하지 않고 재연결 경로로 보냅니다. 0원·잘못된 체결 시각·이전 체결을 거부하고 동일 체결의 불필요한 DB 쓰기를 생략합니다. 체결은 최대 0.25초 단위로 합쳐 기록하며 소켓 종료 시 남은 정상 체결을 반영합니다. 활성 종목이 없어지면 소켓을 닫아 구독을 해제합니다. 실제 계정 연결 성공을 뜻하는 변경은 아닙니다.

## 실행·검증

```powershell
.venv\Scripts\python.exe -m services.market_dashboard.financing --once --codes 005930 000660 005935
.venv\Scripts\python.exe -m services.market_dashboard.financing
```

통합 worker에도 자동 시작을 연결했습니다. 수집기 CLI는 `storage/public-market/runtime.env` 또는 `MARKET_ENV_FILE`에서 허용된 공급자 키만 읽습니다. 기존 프로세스 환경변수를 우선하고 공개 웹 변수·과거 로그인 키는 불러오지 않습니다.

- 관련 Python 검사 67개 통과: 날짜·단위·결측·스키마·페이지 실패·캐시 갱신·키 로딩·구독 거절·중복 체결 포함.
- 운영 빌드 브라우저 검사 25개 통과. 대차·신용 큰 글씨·다크모드 보강 후 관련 2개 추가 실행도 통과.
- Next 운영 빌드, TypeScript, 변경 TSX ESLint, Python Ruff 통과.
- `screenshots/mobile-financing-contract*.png`는 명시적인 브라우저 계약 fixture입니다. DB에 넣지 않았으며 실제 시장 수치 증거로 사용하지 않습니다.
- `screenshots/mobile-financing-live.png`는 실제 로컬 API 상태입니다. 세 대표 종목의 수집 실행 결과는 모두 unavailable입니다. 현재 실행 설정에 한투 키가 없으므로 실제 대차·신용 자료는 확보하지 못했습니다.
- 공매도 외부 조회는 다시 ConnectError였고 SSH는 Permission denied였습니다. 이번 변경의 공개 서버 반영과 실제 장중 틱 수신은 미완료입니다. 사용자에게 기존 키 파일 경로만 요청했으며 비밀키 재전송은 요구하지 않았습니다.

로컬 운영 빌드는 `.next-financing-release`, 웹 3216·API 8216입니다. 자동 검사 통과가 모든 종목의 모든 지표 확보나 아버님의 실기기 평가 통과를 뜻하지 않습니다.
