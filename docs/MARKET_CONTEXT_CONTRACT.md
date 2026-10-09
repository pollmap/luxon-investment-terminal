# 날짜별 시장·뉴스·관련 기업 조회

2026-10-09 KST. `services/market_dashboard/market_context.py` 기준입니다.

## 연결

앱 초기화에서 기존 API를 유지한 채 등록합니다.

```python
from .market_context import attach_routes as attach_context_routes
attach_context_routes(app, BASE, lambda: get_store())
```

기존 수집 작업자의 `asyncio.gather`에 다음 작업을 추가합니다.

```python
from .market_context import market_context_loop
market_context_loop(store)
```

일회 실행에서는 `await market_context_loop(store, once=True)`를 사용합니다. 작업자는 기존 뉴스와 종목·지수 스냅샷을 정규화합니다. 추가 공급자 API를 호출하지 않으며, 공개 조회 경로는 테이블 생성·수집 요청·자료 쓰기를 하지 않습니다.

## 조회 계약

| API | 인자 | 주요 응답 |
|---|---|---|
| `/api/market/v1/market-brief` | `date=YYYY-MM-DD` 선택 | 거래일, 시장별 관측 집계, 거래대금 상위 종목, 같은 날짜 지수·뉴스, 저장된 날짜 목록 |
| `/api/market/v1/news` | `date`, `code`, `limit` 1~100, `offset` 0~10000 | 제목·원문·발행일·관련 종목, 다음 페이지 여부 |
| `/api/market/v1/securities/{code}/relations` | `date` 선택 | 검토된 사업 관계와 같은 관련 기사 연결, 각각의 근거·유효 기간·검토 상태 |

공개 쿼리 이름은 **`date`**입니다. 내부 repository 메서드만 `day` 인자를 사용합니다. 범위 밖 종목은 404, 잘못된 날짜·미래 날짜·페이지 범위는 422입니다.

날짜를 생략한 시장 요약은 **마지막으로 저장된 실제 거래일**을 보여줍니다. 요청한 날짜의 저장본이 없으면 `not_collected`와 빈 시장 결과를 반환합니다. 현재 순위나 다른 거래일의 지수를 복사하지 않습니다. 자료의 거래일과 서버 기록 시각을 분리합니다.

시장 집계는 KOSPI·KOSDAQ의 보통주·우선주 관측 범위에 한정하며 시장 전체 거래대금이라고 확대하지 않습니다. KRX·통합 등 거래시장 범위가 다른 관측은 별도 `groups`에 둡니다. 서로 다른 날짜의 가격은 합산하지 않고 `excluded_other_dates`에 집계합니다. 거래대금 미수집은 0으로 바꾸지 않습니다.

## 저장과 보존

| 테이블 | 보존 단위 |
|---|---|
| `market_context_documents` | URL 기준 문서 ID와 실제 제목·발행일 변경 버전. 기사 전문은 저장·공개하지 않음 |
| `market_context_document_securities` | 공급자가 해당 종목 뉴스로 연결한 문서·종목 조합 |
| `market_context_briefs` | 날짜마다 마지막 관측 1개. 각 거래시장 상위 50개와 관측 전체 집계 |
| `market_context_relations` | 문서 근거를 가진 사업 관계의 검토·유효 기간 변경 버전 |

기존 테이블과 원본은 변경하지 않습니다. 정정 제목은 이전 버전을 보존합니다. 같은 기사를 다시 가져온 시각이나 뉴스 목록 응답 해시가 바뀌어도 새로운 기사 버전으로 늘리지 않습니다. 시장 요약은 분마다 전체 종목 이력을 누적하지 않아 디스크 증가를 제한합니다. 원본·정규화 가격 이력은 기존 수집 구조가 담당합니다.

새 보존은 **배포 후 수집 시점부터** 축적합니다. 현재 남은 종목별 30건보다 이전의 뉴스가 모두 확보됐다고 주장하지 않습니다. 뉴스 날짜 조회는 `latest_recorded_revision`이며 당시 시점의 알려진 정보만을 재현하는 투자 백테스트 계약은 아닙니다.

## 관계의 의미

- `shared_news`: 같은 원문을 공급자가 두 종목의 관련 뉴스로 연결했습니다. UI 문구는 **같은 관련 기사**입니다. 사업 관계·뉴스 본문의 공동 언급·가격 상승 원인으로 해석하지 않습니다.
- 사업 관계: `supplier / customer / subsidiary / shareholder / business_partner`를 지원합니다. 원문 URL·문서 ID·근거 구절·공개일·유효 기간이 필수이며 `reviewed`만 공개합니다.
- `candidate`: 검토 전 후보는 저장할 수 있지만 사업 관계 조회에 공개하지 않습니다. 현재 이 모듈은 AI 추출을 실행하지 않습니다.
- 알려진 사업 관계를 임의로 시드하지 않습니다. 자료가 없으면 `no_observations`입니다.

`save_relation()`은 내부 검토 결과 적재 함수이며 공개 쓰기 API가 아닙니다. 검토 담당자가 원문과 구절을 대조한 결과만 `reviewed`로 넣어야 합니다. 아직 사업보고서에서 관계를 자동 추출·검토하는 전체 파이프라인을 완료한 것은 아닙니다.

## 검증과 현재 한계

```powershell
.venv/Scripts/python.exe -m pytest tests/test_market_context.py tests/test_market_news.py -q
```

14개 검사 통과: 잘못된 기준일 혼합, 없는 과거 날짜의 현재값 복사, 거래시장 혼합, 결측의 0 변환, 정정 소실, 기사 중복, 기사 본문·민감 필드 노출, 범위 밖 종목, 근거 없는 사업 관계, 날짜·페이지 입력, 읽기 중 쓰기, 기존 뉴스의 증분 변환을 검사했습니다. 테스트 데이터는 `fixture_non_production`입니다.

2026-10-09 00:15 KST에 기존 공개 서비스의 삼성전자·SK하이닉스·현대차 뉴스 각 30건을 읽어 **로컬 메모리 DB에서 실제 기존 자료를 변환**했습니다. 90개 연결이 기사 81개로 중복 제거됐으며 삼성전자·SK하이닉스의 같은 관련 기사 9개를 확인했습니다. 실제 발행 범위는 10월 4일~5일이며 새 기사 수집이나 현재 시세 검증이 아닙니다. 기록은 `work/operations-20261008/context-live-check/result.json`입니다.

구현·로컬 검사·기존 자료 변환을 확인했습니다. 새 공개 API의 배포, 작업자 재시작 뒤 지속 수집, 화면 조작 검증은 상위 통합 작업에서 확인해야 합니다. 사업 관계의 검토된 실제 적재, 과거 뉴스 보완 수집, 역사적 업종 구성의 전 기간 확보는 이 기능이 존재한다는 이유로 완료 처리하지 않습니다.
