# v136·v137 상품 변경 코드 검토

## 최종 통합 상태 · 2026-10-13 v139

아래 검토 메모는 수정 당시의 기록입니다. 최종 앱 소스 `ecae992`는 [CI38401937553](https://github.com/pollmap/luxon-investment-terminal/actions/runs/38401937553)의 Python2,836·Node169·Playwright50·타입·정적 빌드 검사를 통과했고 v139로 공개 반영됐습니다. 실제 PC에서 재무 기간·지표 전환·가격 날짜 보존과 새 버전 안내를 확인했습니다. 이후 운영 도구 `4fc1e43`은 release provenance 상속 방지로 별도65 fixture·F lint를 통과했으며 앱 배포 소스와 구분합니다. 전체 자료 확보·복원·실시간·공개 성능은 통과하지 않았습니다. 상세 결과는 [ACCEPTANCE](ACCEPTANCE.md)입니다.

검토 시점: 2026-10-13 KST. 현재 작업 트리의 화면·검색·재무 기간·실시간 원본 저장·CI 변경을 읽었습니다. 공개 v137 배포와 빌드는 통합 담당자의 확인이며, 이 검토에서 공개 주소를 다시 조회하지 않았습니다.

**검토한 범위에서 P0·P1 회귀나 부족한 import는 발견하지 않았습니다.** 재무 기간 표시와 검색 접근성에서 두 결함을 발견해 승인된 최소 수정과 Node 회귀를 추가했습니다. 이 두 수정은 작업 트리에 있으며 **v138 빌드·공개 반영이 필요합니다.** 코드 검토를 전체 자료 확보·복원·실시간·성능 통과로 해석하지 않습니다.

## 발견한 결함과 수정

| 항목 | 발견한 문제 | 조치·검증 |
|---|---|---|
| 재무 기간 표시 | `displayContext`가 실제 날짜만 있는 다른 계정을 unknown 계정의 표시 기간으로 사용할 수 있었습니다. 해당 계정의 `period_verified`와 충돌 사유를 확인하지 않았습니다. | `candidate.period_verified === true && !candidate.period_reason` 조건을 추가했습니다. 유일한 실제 기간이 미검증 또는 충돌인 경우 unknown 기간을 유지하는 회귀를 추가했습니다. 원문 금액을 보존하며 증감률·이익률의 검증 gate를 승격하지 않습니다. |
| 검색 접근성 | Escape로 검색 결과를 닫아도 선택 index가 남아 `aria-activedescendant`가 제거된 option ID를 가리켰습니다. | 팝업이 열린 경우에만 active descendant를 제공합니다. 실제 HeaderSearch의 상태·Escape 이벤트를 사용하는 Node 회귀에서 목록 닫힘, `aria-expanded=false`, active descendant 제거를 확인했습니다. |

수정 파일은 `apps/web/components/market/earnings-overview.tsx`, `header-search.tsx`와 해당 두 Node 검사 파일입니다. 다른 상품 소스는 이 검토에서 변경하지 않았습니다.

## 구조·계약 확인

- **재무:** 실제 기간별 identity, 공개시각에 따른 정정 선택, 원문 금액 유지, 동일 회사·단위 조건을 보존합니다. 검증하지 않은 기간의 숫자를 검증된 성장률로 바꾸지 않습니다.
- **검색:** 이름·코드·초성·별칭의 식별 projection과 반환 후보 시세 결합이 분리됩니다. 좁은 검색의 캐시 의존성은 해당 종목의 quote head이며, 다른 종목의 가격 갱신은 응답을 무효화하지 않습니다. 조회 후 버전 재검증과 legacy fallback을 유지합니다.
- **남은 검색 한계:** 빈 검색과 일치 종목 30개 초과는 기존 전체 순위 경로를 사용합니다. 기존 거래대금 정렬과 초기 limit 밖 종목의 순위 승격을 보존하기 위한 선택이며, 이 경로의 성능 개선은 완료로 표시하지 않습니다.
- **차트·화면:** API의 요청·표시·저장 기간과 실적 비교 구간을 구분합니다. 넓은 저장 기간이나 `backfill_complete`만으로 전체 검증 완료를 선언하지 않습니다. 비교선은 서버 eligibility를 따르고 결측·음수·미검증 값을 브라우저에서 복구하지 않습니다.
- **실시간 원본:** 새 `toss-stream` 저장은 SHA가 가리키는 전달받은 UTF-8 바이트를 하나의 create-only envelope에 보관합니다. 기존 `.bin`은 보존하고, 이미 있는 envelope는 전체 원본 읽기·해시 검증으로 대조합니다. `atomic_create`는 파일 fsync·원자적 게시·임시 파일 정리를 수행합니다. 양쪽 기존 reader는 envelope-only 경로를 지원합니다. 구독 승인·원본 보관을 장중 화면 검증이나 무손실 수신의 증거로 바꾸지 않습니다.
- **CI:** `.github/workflows/market.yml`에 기존 Node glob과 새 `compact-workspace.spec.ts`가 포함돼 있습니다. Python은 현재 root fixture 목록을 수집합니다. 명령 구성의 누락은 발견하지 않았지만, 원격 CI의 실제 성공은 별도 상태입니다.
- **출처:** Lucian separator의 출처 주석과 MIT 고지가 포함돼 있으며, 금융 fixture·데모 데이터는 운영 자료로 채택하지 않았습니다.

## 검증 상태

| 검사 | 결과·범위 |
|---|---|
| 이번 재무 Node 단독 검사 | `analysis-periods.node.test.mjs` 18개 통과, 6.43초 |
| 이번 접근성 Node 단독 검사 | `frontend-interactions.node.test.mjs` 6개 통과, 3.13초 |
| diff 공백 검사 | 검토 대상 `git diff --check` 통과. LF/CRLF 경고는 별도 줄 끝 설정 안내이며 공백 오류가 아닙니다. |
| 이전 검색 fixture | 후보 검색 수정 단계의 isolated pytest 68개·F lint 통과. 이번 검토에서는 다시 실행하지 않았습니다. |
| 이전 브라우저 fixture | v136 정적 후보의 50개 통과. 이번 두 수정 이후 브라우저 검사는 추가하지 않았습니다. |
| 빌드·타입·실제 브라우저·부하 시험 | 복원 작업의 자원 보존을 위해 이번 검토에서 실행하지 않았습니다. v138 통합 검사·CI로 확인해야 합니다. |

현재 `git status`의 새 구현 파일과 CI 변경은 아직 작업 트리에 있습니다. GitHub 반영 시 새 helper·검사·라이선스 파일을 선택적으로 포함해야 하며, 오래된 미추적 자료나 관련 없는 사용자 변경을 일괄 추가하지 않습니다.

## 공개 반영 전 남은 확인

v138에서 두 작은 수정의 타입·정적 빌드와 CI를 확인하고, 배포 커밋·자산 버전을 대조해야 합니다. 실제 모바일·아버님 사용 과제, 전체 복원, 전 종목 자료 확보, 장중 체결·호가의 공개 화면 반영, 공개 인터넷 성능 목표는 각각의 독립된 완료 조건으로 유지합니다.

## 토스 페이지·구독 후속 검토

공식 REST 1.2.24에서 `nextUntil`·`nextBefore`는 선택 필드이며, 마지막 페이지는 명시적인 null입니다. 기존 거래·지수 수집은 필드 누락을 null과 같은 것으로 처리해 전체 이력을 완료로 잘못 표시할 수 있었습니다.

- 거래·지수의 커서가 누락되면 유효 행은 보존하고 이전 커서·`cursor_unknown`을 기록합니다. 같은 burst에서 동일 미확정 페이지를 반복하지 않습니다.
- 명시적 null은 Toss 원본의 64자리 해시·문서 참조와 일치하는 영수증이 있을 때만 종단으로 기록합니다. 이전 영수증도 source·hash·종목·자료 종류·시장 문맥을 검사합니다. 실제 원문 내용과 전체 이력 검증은 별도 감사입니다.
- Async 1.2.2의 부분 거부 대상을 채널별로 후속 full-replace 선언에서 제외합니다. 정상 가격, `Store.active()`와 REST 복구 수요는 유지합니다. 미승인 프레임은 원본을 보존하고 가격·호가 projection에서 제외합니다.
- 실제 수신 frame을 JSON 해석 전에 한 번 보관하고 `archived_digest`로 projection에 전달하도록 통합했습니다. JSON 재직렬화로 원본 SHA를 바꾸지 않습니다. 공식 keepalive는 텍스트 `PING`에 JSON `{"type":"pong"}` 응답입니다.

검증은 공급자·운영 DB에 접근하지 않은 fixture입니다. 거래 커서·지수·기존 Toss·WS 거부 회귀 **63개 통과(17.64초)**, F lint 통과입니다. 이 시험 이후 추가한 정확한 수신 frame 전달은 root의 `stream_projection.record(archived_digest=...)` 및 원문 whitespace 회귀와 함께 최종 통합 검사해야 합니다. 이 기록은 장중 실제 수신이나 공개 반영의 통과를 의미하지 않습니다.

추가 가격 이력 검사 **6개 통과(8.77초)**입니다. strict validator의 기존 누락 거부, collector의 optional cursor 허용, 유효 OHLC·이전 cursor 보존, 가격 receipt의 회사·history/raw-history·통합 시장·조정 기준, legacy complete 보존과 proof 미확정 표시를 확인했습니다. `history_terminal_status`는 종단 근거 상태이며, 전행·시계열 무결성 검증의 대체가 아닙니다. 최종 F lint와 해당 diff 공백 검사도 통과했습니다.

전체 CI의 후속 실패에서 기존 synthetic 출처·짧은 SHA를 종단 근거로 기대하던 fixture와 거래의 legacy 완료 이력이 빈 최신 응답에서 지워지는 회귀를 분리했습니다. `tests/toss_fixture_evidence.py`는 모의 응답 원문을 실제로 보관한 64자리 SHA를 사용하며 `fixture_non_production=true`를 유지합니다. 누락 cursor나 형식 오류를 자동 보정하지 않습니다. 거래의 기존 완료 주장은 유지하고, 근거가 없는 경우 `history_terminal_status=legacy_unverified`로 구분합니다. 빈 또는 비종단 최신 페이지는 기존 종단 원문 참조를 덮어쓰지 않습니다. 공식 응답 `nextUntil`과 요청 `until`의 `format=date`·inclusive `YYYY-MM-DD`를 확인해 숫자형 시험 커서를 날짜로 수정했습니다. 관련 backfill·collection·페이지 회귀 **133개 통과(29.34초)**, F lint·diff-check 통과입니다. 전체 CI와 공개 반영은 root의 최종 통합 단계입니다.
