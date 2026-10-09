> 과거 기록입니다. 키움·한투 도구와 실행 연결은 2026-10-03 토스 단일 전환에서 제거했습니다. 현재 설정은 README-MARKET.md를 따릅니다.

# 증권사 도구 연결 — 2026-09-27

## 현재 결과

키움 공식 SETUP-MCP.md·SETUP-CLI.md, 한투 공식 MCP 설명과 Codex MCP 설정 문서를 확인했습니다. 기존 키는 재사용하지 않고 새로 발급받는다는 요청을 반영했습니다.

설치는 완료되지 않았습니다. 이 실행 환경에서 GitHub HTTPS 연결 실패, uv.exe 실행 접근 거부가 발생했습니다. pip의 kwcli 다운로드도 설치 가능한 패키지를 찾지 못했습니다. 설치된 것처럼 MCP 항목만 등록하지 않았습니다.

`scripts/install-broker-tools.ps1`는 키움 명세 MCP·조회 MCP, kwcli, 한투 코드 검색 MCP를 설치하고 `initialize → tools/list`로 검사합니다. 검사 통과 후 `-RegisterCodex` 옵션으로 기존 항목을 보존하며 Codex에 추가합니다. 실제 주문 도구는 꺼집니다. 기존 같은 이름의 설정은 덮어쓰지 않습니다. 현재는 스크립트 구문 검사만 통과했으며 설치·도구 목록 검증은 미실행입니다.

네트워크와 도구 실행이 허용되는 PowerShell에서 프로젝트 폴더로 이동한 후:

```powershell
.\scripts\install-broker-tools.ps1 -RegisterCodex
```

실행 결과는 `work/broker-tools/verification.json`에 저장됩니다. 새 MCP는 Codex를 다시 열어 확인합니다.

## 신청 진행 상태 — 2026-09-27 직접 확인

사용자가 API 신청부터 조회 연결까지 위임했습니다. 추가 작업 허락은 필요하지 않습니다. 본인 인증과 실제 실행 환경 제한만 별도입니다.

| 대상 | 직접 수행한 동작 | 확인된 결과 |
|---|---|---|
| 키움 | 인앱 브라우저에서 공식 홈 접속, 홈 버튼, 공식 /main/home 경로 확인 | 모두 CD-14005 오류 화면. 신청서·로그인 화면에 도달하지 못함. 오류의 정확한 원인은 미확인 |
| 한투 | 개발자센터의 API신청 링크로 이동 | 고객보안 서비스로 이동. VeraPort·K-Defense 미설치 표시 |
| 한투 로그인 | 공식 로그인 링크와 로그인 페이지 진입 | 같은 보안프로그램 설치 화면으로 이동. 본인 로그인 미완료 |
| 발급·호출 | 새 키 발급 여부 | 발급되지 않음. 새 키 인증·실제 조회 검증도 미완료 |

한투 신청 주소: https://securities.koreainvestment.com/main/customer/systemdown/RestAPIService.jsp

사용자의 후속 요청으로 사용 중인 Chrome에 연결했습니다. 기존 키움 탭의 실제 화면에서도 CD-14005를 확인했습니다. Chrome에서 한투 신청 페이지를 열었으나 VeraPort·K-Defense 미설치 화면이 동일하게 나왔습니다. 공식 통합설치프로그램다운로드 버튼은 클릭했습니다. 다운로드 완료·실행은 확인하지 못했습니다. 도구가 chrome://downloads/ 접근을 보안 정책으로 거부해 다른 경로로 우회하지 않고 사용자에게 설치와 본인 로그인을 요청했습니다. 이후 작업 브라우저는 Chrome을 유지합니다.

시스템 보안프로그램 설치 위치는 현재 실행 환경의 쓰기 허용 범위 밖입니다. 열어 둔 인앱 브라우저에서 필수 설치와 본인 로그인을 완료해 달라고 요청했습니다. 비밀번호·인증번호·API 키를 채팅에 요청하지 않았습니다. 키움 오류를 계정 문제나 서비스 전체 장애로 단정하지 않습니다.

서버의 한투 호출은 현재 쓰는 6개 시세 조회 경로와 거래 ID 조합으로 제한했습니다. 주문·계좌·미등록 경로는 토큰 요청 이전에 거부합니다. 기존 조회의 GET 방식 유지와 차단 검증을 포함하여 관련 테스트 42개가 통과했습니다. 이는 모의 HTTP를 포함한 코드 검증이며 실제 증권사 인증 성공을 의미하지 않습니다.

## 인증 후 연결 절차

1. 키움 개발자센터에서 REST API 이용 신청 후 앱 키·시크릿을 발급합니다. 모의투자와 실전 키는 구분합니다.
2. 설치 스크립트 마지막에 표시되는 `kiwoomcli.exe setup` 명령을 본인의 PowerShell에서 실행합니다. 공식 CLI는 키를 숨김 입력으로 받아 OS 자격 증명 저장소에 저장하므로, 이 입력은 사용자가 직접 합니다. 키를 채팅에 붙이지 않습니다.
3. 등록 후 `kiwoomcli auth list`, `kiwoomcli doctor`에서 조회 가능 상태를 확인합니다. CLI 등록만으로 별도 MCP와 대시보드 서버의 인증이 자동 연결되는 것은 아닙니다. 키움 MCP 설정의 APP_KEY·APP_SECRET 자리와 대시보드 worker 환경도 별도로 연결해야 합니다.
4. 한투는 계좌·ID 연결 및 Open API 서비스 신청 후 App Key·App Secret을 발급합니다. 대시보드 worker의 `MARKET_KIS_APP_KEY`, `MARKET_KIS_APP_SECRET`에 연결합니다. 키움 worker는 `MARKET_KIWOOM_APP_KEY`, `MARKET_KIWOOM_SECRET_KEY`입니다. 브라우저 앱에는 키를 넣지 않습니다.

## 한투 도구의 용도

- KIS Code Assistant MCP: 공식 API 검색·예제 확인. 이번 설치 스크립트의 대상입니다.
- KIS Trading MCP: 실제 API 실행. 주문 기능까지 포함하므로 공개 조회 앱에 그대로 노출하지 않습니다. 기존 서버의 조회 어댑터에 필요한 기능만 연결합니다.
- kis-ai-extensions: 공식 전략·백테스트·주문 플러그인입니다. 키움 kwcli와 동일한 범용 조회 CLI라고 부르지 않습니다. 이번 공개 대시보드의 기본 구성에 추가하지 않았습니다.

## 공식 문서

- [키움 MCP 설치](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/SETUP-MCP.md)
- [키움 CLI 설치·본인 키 등록](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/SETUP-CLI.md)
- [키움 개발자센터](https://openapi.kiwoom.com/)
- [한투 MCP 연결과 API 신청](https://github.com/koreainvestment/open-trading-api/blob/main/MCP/MCP%20AI%20%EB%8F%84%EA%B5%AC%20%EC%97%B0%EA%B2%B0%20%EB%B0%A9%EB%B2%95.md)
- [한투 코드 검색 MCP](https://github.com/koreainvestment/open-trading-api/blob/main/MCP/KIS%20Code%20Assistant%20MCP/README.md)
- [한투 전략 도구](https://github.com/koreainvestment/kis-ai-extensions)
- [Codex MCP 설정](https://developers.openai.com/codex/mcp/)
