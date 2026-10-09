# v44 — 종목별 과거 자료 수집 순서 개선

공개 서버 버전 `20261004-history-schedule-v44`. 화면·주소·DB 원본·사용자 설정·다른 VPS 서비스는 유지했다. 이번 변경은 수집 순서이며 공개 응답 성능 목표를 통과한 변경으로 보고하지 않는다.

## 문제와 변경

이전에는 일봉 전 종목 → 투자자 전 종목 → 프로그램 전 종목 순서로 전체 시장을 순회했다. 같은 종목의 신용·대차 조회가 다른 자료 수천 종목 뒤에 놓였다. 이제 같은 종목의 일봉·투자자·프로그램·공매도 거래·신용·대차·원주가 일봉을 순서대로 처리한 후 다음 종목으로 이동한다.

기존 next pair(cursor, phase)를 그대로 다음 작업으로 사용한다. 종목별 과거 커서·수집 행·재시도·최신 갱신 시각을 초기화하지 않는다. 새 진행 기록에 schedule_version=2, schedule=security_then_kind를 남긴다. 보고 있는 종목의 우선 작업은 기존대로 5회 중 1회이며 전체 시장 커서를 움직이지 않는다. 한 종목의 404가 다음 자료와 다른 종목을 멈추지 않는다.

기존 공유 인증·요청 그룹별 속도 제한·최대 8페이지 기본 burst를 유지했다. 동시 수집기를 새로 만들거나 호출량 제한을 높이지 않았다. 한 종목의 여러 자료를 가까운 순서로 완료하는 개선이며 전체 수집 시간 단축 배수는 측정하지 않았다.

## 검증

관련 테스트 55개 통과(실패 0): 재시작, 기존 phase 중간에서의 전환, 전체 7종의 공정한 순환, 우선 작업과 전체 커서의 분리, 종목 오류 뒤 다음 자료 진행, 긴 일봉의 최대 페이지 제한, 기존 거래 자료 정규화·범위 조회 회귀.

배포 전 attempts 49,421, cursor 2,276, phase 3이었다. 배포 전까지 기존 worker가 더 진행했고, 새 worker 첫 관찰에서는 attempts 49,423, cursor 2,278, phase 3이었다. 이후 새 일정에서 같은 종목의 다음 자료를 처리했다. 큐를 수동 초기화하지 않았다.

실제 운영 관찰에서 295310 종목은 attempts 49,428 → 49,433 동안 cursor 2,279를 유지하고 phase 1 → 6으로 진행했다. 마지막 페이지 확인 전후 저장 상태는 다음과 같다.

| 자료 | 이전 행 | 이후 행 | 이후 큐 상태 | 마지막 페이지 확인 |
| --- | ---: | ---: | --- | --- |
| investor-trading | 100 | 550 | ready | True |
| program-trades | 200 | 549 | ready | True |
| short-selling | 100 | 458 | ready | True |
| credit-trades | 100 | 531 | ready | True |
| securities-lending | 100 | 491 | ready | True |

위 5개 작업의 next_until은 null이고 backfill_complete=true다. 단순 ready 변경이 아니라 기존 과거 커서를 통해 실제 저장 행이 증가했다. source 전체 기간의 수집 상태이며 회계·기업행사·UI 무결성이나 서비스 전체 완료를 대신하지 않는다.

공개 health의 버전 v44, 삼성전자 요약의 code=005930·HTTP 200, 종목 페이지 HTTP 200을 확인했다. 앞선 v43a의 ready인데 과거 커서가 남는 잘못된 거래 작업은 최종 감사에서도 0건이었다.

근거: `work/remaining-v44/tests.xml`, `before.json`, `progression.json`, `after.json`, `progression-live.json`, `final-audit.json`, `public.json`. progression.json의 최초 재시작 관찰에는 아직 진행이 없었고 이후 progression-live.json에서 실제 진행을 확인했다.

## 계속 남은 완료 기준

전 종목 과거 자료·404·미등록 작업, 기업별 TTM와 분할 확대, 배당락일·지급일·기업행사와 재투자, 실제 거래일 체결·호가·장애 복구, 공개 인터넷 100동시 성능, 아버님의 실사용은 아직 완료가 아니다. 기존 공개 성능 목표 미달을 이 변경으로 해결했다고 주장하지 않는다. 2026-10-04는 일요일이며 실시간 구독 승인을 새 장중 수신으로 처리하지 않는다.

최종 감사 시각: 2026-10-04T06:44:05.126970+00:00

- credit-trades: collecting=2662, error=110, no_data=61, ready=41
- history: collecting=2688, error=109, no_data=1, ready=76
- investor-trading: collecting=2729, error=110, ready=35
- program-trades: collecting=2551, error=110, no_data=118, ready=94
- raw-history: collecting=2686, error=109, no_data=1, ready=79
- securities-lending: collecting=2673, error=110, no_data=62, ready=29
- short-selling: collecting=2370, error=110, no_data=82, ready=312

404 작업 768건(동일 종목의 여러 자료 포함), 잘못된 완료 상태 0건.
