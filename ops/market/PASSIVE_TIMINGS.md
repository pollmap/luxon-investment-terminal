# 주식 서비스의 응답 시간 수동 관측

이 도구는 **설정 파일 생성과 기존 로그 분석만** 수행합니다. 요청 생성, SSH, 설정 설치,
서비스 재시작, 앱 변경을 하지 않습니다. 현재 Nginx vhost를 오래된
`nginx-public.conf` 템플릿으로 덮어쓰지 않습니다.

## 지금 확인할 질문

v101의 HTTP/2 100동시 원시 기록에는 41바이트 응답도 헤더 이후 약 549ms의 클라이언트
완료 대기가 있습니다. 같은 기록의 앱 `Server-Timing`은 헤더 준비까지 측정합니다.
따라서 큰 JSON, DB 잠금, 인터넷 중 하나로 나머지 지연을 단정할 수 없습니다.
기존 단건 관측의 RTT 약 287ms는 별도 사실이며 100동시 전체 지연을 설명하지 않습니다.

이 관측은 **새 부하를 발생시키지 않고** 평소 요청의 Nginx 구간을 나눕니다.
PC 백업 전송 중에는 인터넷 성능 비교나 목표 통과 판정을 하지 않습니다.

## 검토용 설정 생성

```powershell
.venv/Scripts/python.exe ops/market/passive_timings.py render --release v102-observation --output work/passive-timings-review --minutes 30 --inherited-access-log "access_log /var/log/nginx/access.log combined;"
```

- 출력 경로는 새 디렉터리여야 합니다. 기존 설정이나 증거를 덮어쓰지 않습니다.
- 위 `--inherited-access-log`는 사용법 예시입니다. 운영자가 **현재 상속되는 실제 지시문**을
  확인해 format·옵션까지 그대로 지정해야 합니다. 여러 개면 인자를 반복합니다. 누락하거나
  `off`를 지정하면 생성하지 않습니다. 전역 로그 설정 자체는 바꾸지 않습니다.
- 기본 관측 창은 생성 시점부터 30분입니다. `--start-epoch`으로 설치 예정 시각을 지정하거나
  실제 적용 직전에 다시 생성합니다. 최대 30분이며 Nginx map 자체가 창 밖 기록을 차단합니다.
- release는 **운영자가 확인한 관측 대상 앱 버전**입니다. 자동으로 실행 버전을 검증하지
  않습니다. 혼합 버전이면 관측을 끄거나 배포 후 다시 생성해야 합니다.
- 생성한 `window.json`으로 시작·종료 시각을 확인합니다.
- `--source-vhost work/operations-20261009-v94/nginx-before-passive-v103.conf`를 함께 주면
  실제 vhost 사본에만 적용한 `vhost-preview.conf`와 원본 SHA256도 생성합니다. 두 server의
  이름과 HTTPS 시작부를 엄격히 확인하며 기존 access_log가 있으면 자동 삽입을 거부합니다.
  원본 파일은 수정하지 않습니다. 부모 작업은 실제 운영 파일 SHA가 여전히 같은지 확인해야 합니다.

생성되는 파일의 역할:

| 파일 | 운영자가 검토 후 적용할 위치·역할 |
|---|---|
| `timings-http.conf` | `http` 문맥의 map·log_format. 다른 vhost의 access_log는 변경하지 않음 |
| `timings-vhost.conf` | 상속 로그를 동일하게 명시한 뒤 관측 로그 추가. 명시적인 location 로그가 있으면 해당 위치도 확인 |
| `logrotate.conf` | `/etc/korea-stocks-timing/logrotate.conf` 별도 보존 규칙 |
| `korea-stocks-timing-rotate.service/timer` | 전용 상태 파일로 해당 로그만 1분마다 용량 점검 |
| `window.json` | 설치·판독할 관측 창과 지정 버전 |

운영 반영은 부모 작업에서 수행합니다. 적용 전 해당 vhost의 `access_log` 상속과
location별 `access_log off`/별도 로그를 확인해야 합니다. 새 규칙과 기존 combined 로그가
동일 위치에 함께 있으면 기존 로그는 여전히 민감한 요청 경로를 남길 수 있습니다.
이 도구가 기존 전체 로그를 정리하거나 익명화했다고 간주하지 않습니다.
전달받은 2026-10-10 vhost 사본에는 access_log가 없으므로 HTTPS server에 새 전용 로그만
추가하면 상위 기본 로그가 사라집니다. 이를 막기 위해 기존 상속 지시문을 동일하게 명시합니다.
관측 창 종료 후에도 이 기존 로그는 계속 남습니다. 전역 지시문과 다른 서비스 로그는 바꾸지
않으며 HTTP→HTTPS 리다이렉트 server의 기존 상속도 유지합니다.

정적 location의 내부 리다이렉트는 원래 `$request_uri`를 분류하므로 `/stocks.html`도
페이지 범주로 남습니다. GET·HEAD 공개 읽기와 정적 페이지·자산만 기록합니다.
인증·계좌·내부 진단·알 수 없는 경로·WebSocket은 기록하지 않습니다.

설치 점검 순서:

1. 주식 vhost 원본을 보존하고 Nginx 실행 버전과 대상 앱 버전을 확인합니다.
2. `/var/log/nginx/korea-stocks-timing`과 `/var/lib/korea-stocks-timing`을 root 소유·0700으로
   생성하고 `access.jsonl`을 root 소유·0600으로 준비합니다. 심볼릭 링크이면 중단합니다.
3. 전용 logrotate 설정·상태 파일·타이머를 먼저 확인합니다. 시스템 공통 logrotate 규칙에
   같은 파일을 중복 등록하지 않습니다. `logrotate --debug`로 대상이 이 파일 하나인지 검사합니다.
4. http map과 해당 vhost access_log만 반영하고 `nginx -t` 통과 뒤 부모 작업에서 reload합니다.
   cache TTL·gzip·keepalive·라우팅·버퍼 설정은 변경하지 않습니다.
5. 새 부하 시험 없이 기존 화면 이용 로그 몇 줄만 수집해 파서로 검사합니다. 로테이션 타이머의
   정상 실행, 로그 권한, 종료 시각 이후 추가 기록이 없는지 확인합니다.
6. 종료 시 vhost 관측 로그와 http 정의를 함께 제거하고 검증 후 반영합니다. 타이머를 중지하고
   정제된 분석 결과만 보관한 뒤 **이 전용 로그 경로만** 명시적으로 정리합니다.

## 저장하는 값과 제한

로그에는 고정 route 범주, 지정 release, HTTP status, cache 상태, 기록 시각,
request/connect/header/upstream 시간, 본문·전체 전송 byte만 있습니다.
URL·query·종목코드·토큰·IP·쿠키·User-Agent·Referer·연결 ID는 저장하지 않습니다.
URI와 view는 분류에만 쓰고 원문은 log_format에 포함하지 않습니다.

로그는 5MiB에서 회전하며 현재 파일과 이전 2개만 유지합니다. 전용 타이머가 1분마다
점검하고 30분 뒤 새 기록을 차단합니다. **5MiB는 매 순간의 하드 상한이 아닙니다.**
검사 간격의 증가분, 16KiB Nginx 쓰기 버퍼, copytruncate 중 임시 복사본만큼 넘을 수
있습니다. 디스크가 빠듯하거나 타이머가 실패하면 관측부터 중지합니다.
copytruncate는 다른 서비스 로그에 신호를 보내지 않지만 복사·절단 사이 일부 행이
누락될 수 있습니다. 이 기록은 진단 표본이며 전체 요청 수·결제·감사 증거가 아닙니다.

## 파싱과 해석

```powershell
.venv/Scripts/python.exe ops/market/passive_timings.py summarize work/access.jsonl work/access.jsonl.1 --samples 10
```

파서는 원본을 수정하지 않습니다. 잘못된 행 내용은 출력하지 않으며 32MiB 입력·5만 행·
128개 분류·최대 100개 정제 표본으로 제한합니다. 제한에 도달하거나 큰 비정상 행이 있으면
`truncated=true`를 표시합니다. 샘플에는 종목·사용자 식별자가 없습니다.

실제 Nginx 1.24의 JSON 로그에서 HIT·정적 응답의 upstream 시간은 `""`로 나올 수도
있습니다. 빈 문자열과 `"-"`는 미측정으로 처리하고 0ms로 바꾸지 않습니다.
쉼표 뒤의 빈 항목 등 잘못된 다중 시도 목록은 계속 거부합니다.

| 계산값 | 의미와 한계 |
|---|---|
| `request_ms` | Nginx가 첫 요청 바이트를 읽은 뒤 마지막 응답 바이트를 송신할 때까지. 클라이언트 수신 완료가 아님 |
| `header_ms` | upstream 응답 헤더까지. 앱의 헤더 처리와 동일 측정 시작점이라고 가정하지 않음 |
| `upstream_ms` | upstream 응답 수신 완료까지. 앱 계산 외 대기·전달 포함 |
| `upstream_body_ms` | **동일 요청**의 upstream response−header. 헤더 이후 앱 스케줄링과 본문 전달 구간 |
| `residual_ms` | 동일 요청의 request−upstream. 수신·캐시 대기·Nginx 처리·내려보내기 등이 섞인 잔여값. 순수 인터넷 시간이 아님 |

응답을 스트리밍하면 upstream 수신과 downstream 전송은 겹칠 수 있습니다. 잔여값을
"클라이언트 본문 시간"으로 명명하지 않습니다. 캐시 HIT·정적 파일의 없는 upstream 값을
0ms로 만들지 않습니다. 여러 upstream 시도·실패·모순된 시간은 별도 상태로 세고
단일 구간 차감에서 제외합니다. 각 구간의 표본 수와 p50·p95를 따로 제공합니다.

분석은 API/정적, route, release, status, cache별로 나눕니다. 서로 다른 분포의 p95를
빼서 네트워크 p95를 만들지 않습니다. 이 도구는 식별자를 저장하지 않으므로 HTTP/2
클라이언트의 요청 순서와 로그 순서를 연결하지 않습니다. Nginx 시간이 작고 기존
클라이언트 완료 시간만 큰지, upstream 본문 시간도 큰지를 먼저 구분할 수 있지만,
개별 WAN 요청과 정확한 상관분석이나 TCP 손실 증명은 별도 검증 없이는 불가능합니다.

## 검사

```powershell
.venv/Scripts/python.exe -m pytest ops/market/test_passive_timings.py -q
.venv/Scripts/python.exe -m ruff check ops/market/passive_timings.py ops/market/test_passive_timings.py
```

fixture는 합성 자료입니다. 로컬 검사는 분류·민감값 차단·종료 경계·결측/다중 시도·
동일 요청 차감·보존 규칙을 검사하며 운영 Nginx의 설치·정상 기록을 대신하지 않습니다.

설정과 시간 정의의 근거: [Nginx log module](https://nginx.org/en/docs/http/ngx_http_log_module.html),
[Nginx upstream variables](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#variables),
[Nginx map](https://nginx.org/en/docs/http/ngx_http_map_module.html),
[logrotate 공식 매뉴얼](https://github.com/logrotate/logrotate/blob/main/logrotate.8.in).
