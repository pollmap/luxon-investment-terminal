# 저장공간·백업·독립 복원 감사

2026-10-13 18:43~19:09 KST의 읽기 관찰과 준비 도구 검증입니다. 이 감사 담당자는 PC/VPS 파일 삭제,
Docker prune, 서비스 재시작, DB 시작·복원·전환, 실제 압축을 수행하지 않았습니다.
원본 금융 자료와 미검증 compact 복원 결과를 보존했습니다. 아래 용량은 확보 실적이
아니며, 실제 정리와 배포는 통합 담당자가 현재 참조를 다시 확인한 뒤 수행합니다.

## 1. 공간과 보호 대상

| 대상 | 이번 관찰 | 판단 |
|---|---:|---|
| VPS 루트 총 용량 | 154,894,188,544B | 다른 서비스와 공유 |
| VPS 사용/가용 | 150,040,260,608B / 4,837,150,720B | 사용률 97%, 가용 4.51GiB |
| 80% 미만까지 필요한 추가 회수 | 26,124,909,773B 이상, 약 24.33GiB | 임계치를 높여 해결한 것으로 처리하지 않음 |
| `/srv/services/korea-stocks/incoming` | 683,992KiB, 143개 항목 | 임시 정적 배포본부터 좁게 정리 가능 |
| 같은 경로의 `backups` | 8,879,088KiB | 고유 일별 복구 지점 보존 |
| 같은 경로의 `releases` | 2,550,664KiB, 14개 디렉터리 | 현재·최근 정상·저장 형식 복구 참조 보존 |
| 활성 원본 볼륨 | 8,837,236KiB | `hannun-public-market_market-sources` 보존 |
| PC C: 가용 | 18,062,958,592B, 16.82GiB | 복원·대형 빌드·압축을 동시 실행하지 않음 |

`df -B1`, 정확한 서비스 경로의 제한된 `du`, 컨테이너의 이미지·마운트만 읽었습니다.
주식 API와 작업자는 v135, 이미지 ID는
`c59b921d772f1ff6b86ee2dd61942883ad44b0648d758f6ecf2146a25d1a988f`입니다.
`current`는 `releases/20261013-evidence-bridge-v135`를 가리킵니다. 두 컨테이너가
같은 원본 볼륨을 사용합니다. OpenAlgo의 별도 상태 경로도 활성입니다.

이번 감사의 보호 집합은 최소한 v135·v134·v133·v131 호환 이미지/설정,
공개 정적 v134와 보수적으로 남긴 v132·v131 정적 배포본, OpenAlgo v73입니다.
v109·v110·v112·v113은 기존 보관 기록에 금융 reader·원본 저장 형식·rollback
참조가 남아 있어 나이가 오래됐다는 이유만으로 정리하지 않습니다. v128~v130도
현재 참조·외부 사본의 파일 해시 대조 없이 자동 삭제하지 않습니다.

정확히 지정한 보호 이미지 4개는 `docker image inspect`로 존재를 확인했습니다.
표시된 이미지 크기 162~163MB는 공유 레이어를 포함하므로 회수량으로 더하지 않습니다.
서비스 prefix만 대상으로 한 `docker image ls`도 12초 한도에 걸렸습니다.
추가 이미지의 실제 비공유 회수량은 **미확정**이며, 전체 Docker 정리는 수행하지 않았습니다.

## 2. 우선 정리 후보와 적용 직전 검사

아래 파일은 `incoming`의 비활성 정적 빌드 압축본입니다. 이 경로는 symlink가 아니며
각 파일의 `nlink=1`을 확인했습니다. 현재·최근 정적 산출물과 원본 seed·금융 수집
압축본은 후보에서 제외했습니다. 17개의 파일 데이터 할당 합계는
**389,955,584B(약 0.363GiB)**, 논리 합계는 389,878,233B입니다.

경로 접두사는 `/srv/services/korea-stocks/incoming/`입니다.

| 정확한 파일 | 이번 읽기 SHA-256 |
|---|---|
| `v113-share-review-static-web.tar.gz` | `3889942ecdde9b6768ea2162c1050183dd693808730def011436c461166a1488` |
| `v114-archive-atomic-static-web.tar.gz` | `2f62826495305f982aa719fb46ee424eb2291d09e1960a042772e5d68eabe4f2` |
| `v116-history-contract-static-web.tar.gz` | `e7c06e224552a5881fc7134512598f9516966c741ec827dd302928fb97c7f91a` |
| `v117-search-index-static-web.tar.gz` | `279d74dfeff7c9ea5896b8c2893b1e4776b6556545c5eaefe330d34e5e0ed195` |
| `v118-rank-status-static-web.tar.gz` | `3e65fc00d7c22decaeb3a4f49afd4e0d23e75b364bf488d289e6ec4d72c9f976` |
| `v119-rank-status-static-web.tar.gz` | `a913020da8c21f4a0a5eb9217d93ddd841bda3f440cdb9ecfca44063a3a6a3a3` |
| `v120-rank-status-static-web.tar.gz` | `a8a8f7ca29e7bf626d4f1b280fd8def3b1df279992adf1799c947362a35507ac` |
| `v121-rank-status-static-web.tar.gz` | `48b7f6416dd57160321d171a79505b6427badba4cefd580764cab81729450603` |
| `v122-rank-status-static-web.tar.gz` | `31a9fbcd4ecedbcfe773afb6918a4745f8a7c60c6e0a12ac550bee1586bfda37` |
| `v123-rank-status-static-web.tar.gz` | `767d8a03dd7aea068d6bada9852d2d92bd977807cfe9d145fc7d6332c85d5eb6` |
| `v124-rank-status-static-web.tar.gz` | `03dd3bba7a285ceda0976c886331d285e9372725d91a490851e2fb999aaf6263` |
| `v125-rank-status-static-web.tar.gz` | `455f6c5c9dae2548b50b9bc53352b458dace2005b8d9abbf6c85a55a413f555c` |
| `v126-rank-status-static-web.tar.gz` | `c09a2e5af3fc8526ea2486ce92397481381bdb2082cdf0f0d142e8902c81af02` |
| `v127-rank-status-static-web.tar.gz` | `431a1a4feb21e8441a035decf0ad640db168260bb460b508474b704b9da8f327` |
| `static-20261011-candle-research-v128.tar.gz` | `252d78b9248561a0928174850523638a7fe83f293c9fe17988b54bfdb58b8ea8` |
| `static-20261011-candle-research-v129.tar.gz` | `c2681923c4af671ccfe9254b9944f735c656c28310f63254a62d6c576d46eeb9` |
| `static-20261011-candle-research-v130.tar.gz` | `161d88fa7073cb4ff00e227cbfd06891da6c3700ac007775ee8c648c39e4f878` |

통합 적용 직전에는 해당 파일의 경로·inode·크기·mtime·SHA와 `current`·컨테이너·정적
배포·작업 프로세스의 참조를 다시 확인합니다. 변경됐거나 참조 중이면 제외합니다.
원본 dump·원본 수집 파일·미검증 복원 결과·다른 서비스 경로는 이 목록에 없습니다.
incoming 전체와 releases 전체를 비우더라도 80% 조건을 충족하지 못하므로,
이 작은 정리만으로 대량 수집을 재개하면 안 됩니다.

## 3. PC 검증용 클러스터의 손실 없는 보존 준비

정확한 대상은 다음 한 곳입니다.

`C:/Users/lch68/Documents/Codex/2026-09-24/x20/outputs/korea-stock-dashboard/work/restore-daily-20261010-v106/cluster-data`

- 파일 1,732개, 논리 50,712,188,923B, Windows `GetCompressedFileSizeW`가 보고한
  파일 데이터 저장량 합계 21,529,708,539B(20.05GiB), 하드링크 0개입니다.
  NTFS 디렉터리·파일시스템 메타데이터와 정리 도중의 다른 쓰기는 회수량에 포함하지 않았습니다.
- 절대 경로와 resolve가 같고 symlink/junction이 아니며 `PG_VERSION=17`입니다.
  `postmaster.pid` 없음, 현재 `postgres.exe` 프로세스 0개, 54420 LISTEN 없음,
  **`pg_ctl status` 종료 코드 3**으로 정지 상태를 확인했습니다.
- 고유 소유 증거 `verification.json` SHA는
  `10a1ee205e660b708882d8ed7f19a1e06a2b8a33e653e79239b166ae63b670ed`입니다.
  이 보고서는 해당 일별 dump·독립 port·pg_restore 성공·스키마/행 수 검사·정상 종료를
  기록하지만 **`source_content_equality_verified=false`**입니다.
- 원본 dump 2,372,779,444B와 AES-256-GCM 사본 2,372,779,770B가 현재 존재합니다.
  기존 인증복호화 기록의 원본 SHA는
  `58eb5398f8e7ea9c74a01eb272d7cd848c940a783d6302e7531cda9fab8435a9`,
  암호화 사본 SHA는
  `526e65a903a409503f224b62c6e21cb0ec82c6cb5fd08cd385f9bf5a993ecaaa`입니다.
  이번 감사는 수GB 원본·암호화 사본의 전체 해시나 복호화를 다시 실행하지 않았습니다.
- 코드 참조는 독립 DB를 수동으로 읽는 `diagnose_restored_timeseries.py` 하나입니다.
  현재 운영과 compact resume 경로에서 이 디렉터리를 쓰는 참조는 발견하지 못했습니다.

부분/미검증 복원 결과 보존 원칙에 따라 이 클러스터도 지금 바로 삭제하지 않습니다.
새 `ops/market/preserve_stopped_restore.py`는 위 exact root의 파일 바이트를 ignored
`work/stopped-restore-archives/<새 run id>`에 손실 없이 보존하는 준비 도구입니다.

1. 고정 소유 보고서 SHA, PG17, postmaster 부재, postgres 프로세스 0, pg_ctl status를 검사합니다.
2. source의 symlink/reparse/hardlink를 거부하고, 정렬된 파일 목록·크기·신원을 고정합니다.
3. 결정론적 gzip level 1 tar를 만들며 파일별 SHA를 기록합니다. 파일 전후와 전체 완료 후
   신원을 다시 대조하며, 압축 스트림 전체를 재독하여 모든 파일·크기·SHA와 압축 SHA를 대조합니다.
4. 시작 여유는 명시한 예상 압축 예산＋4GiB 이상, 추가 출력 상한 6GiB, 전체 30분입니다.
   절대 여유 4GiB와 C: 전체 감소량도 검사하므로 다른 작업의 공간 사용과 구분할 수 있습니다.
5. 파일별 checkpoint는 진행 관찰입니다. 실패한 tar에 자동 이어쓰기하거나 반복하지 않습니다.
   실패 시 source와 partial을 보존합니다. 성공해도 원본·검증용 클러스터를 **자동 삭제하지 않습니다.**

실제 압축은 실행하지 않았습니다. 새 도구의 fixture 14개는 파일 바이트·결정론성·변경 감지·
기존 run 보호·경로 탈출·하드링크·읽기 불가 디렉터리·소유 해시·크기/시간/공간 예산·손상/초과 항목을 검사합니다.
Ruff와 fixture 검사가 통과했으며 XML은
`work/valuation-ui-check/preserve-stopped-restore-fixtures.xml`에 있습니다.
이는 실제 50GB 클러스터의 압축·복원 성공 증거가 아닙니다.

14개 fixture의 첫 실행은 본문 종료 뒤 JUnit hostname 조회의 Windows WMI
`0x8007000e` 오류가 발생했습니다. 테스트 실행기에만 고정 fixture hostname/processor를
적용해 재확인한 결과는 종료 코드 0, 14개 통과입니다. 제품의 프로세스/정지 검사는
완화하지 않았습니다. 실제 exact root의 기본 READ ONLY preflight도 종료 코드 0,
`executed=false`, `archive_verified=false`, `source_deleted=false`였습니다.

검증용 보존 명령은 통합 담당자가 새 run ID를 정해 실행합니다.

```powershell
.venv/Scripts/python.exe ops/market/preserve_stopped_restore.py --run --run-id <새_32자리_hex> --estimated-output-bytes 6442450944
```

예상 예산이 실제 gzip 결과를 보장하지 않으므로 6GiB를 넘으면 중단합니다. 보존 archive가
전수 파일 바이트 검증을 통과한 뒤에만 검증용 사본의 정리와 재생성 방법을 별도로 판단합니다.
금융 원본 dump·암호화 사본·키·verification/log와 compact 부분 클러스터는 제외 대상입니다.

## 4. compact 전체 복원의 실제 경계와 예산

대상 원본은 `work/storage-v117/runs/49521be9a50149bba6a66c21342c3bc1/market-targets.dump`,
1,324,924,221B입니다. manifest는 시계열 **17,014,175행**, 원천 참조 **8,071행**을 요구합니다.
원본 SHA는 `6c1861479624e5dfac87bdeb767579a818933ac87836b754490fe27cfcdf1c4c`,
receipt SHA는 `775f29002e4fe2c3088e87834585df87eab4d0b644ad151bf0532932cb34901e`입니다.
이번에는 저장된 RESULT·manifest·로그와 정지 확인 기록만 읽었고 전체 원본 해시와
DB 행 수·ordered hash는 재실행하지 않았습니다.

최신 결과는 `resume-e0aea29743fc4c949d7279e792b2f8f8/RESULT.json`입니다.

- `restore_verified=false`, `state=interrupted`, `data_equal=false`.
- 저장된 원본 전체 SHA 검사는 통과했지만 `checkpoint_counts`에서 SQLSTATE `57014`
  (statement timeout)로 중단됐습니다. 전체 ordered hash와 원천 참조 복원은 미통과입니다.
- `processed=0`, `committed=null`, `verified_prefix=0`, `checkpoint_reload_required=true`.
  오류 진단의 **15,282,400행/8,071참조는 관측 checkpoint**이며 검증 완료가 아닙니다.
- 시도 자체 종료는 112.5초입니다. 초기 RESULT의 종료 실패는 별도 정리 기록에서 해결됐고,
  `restore-final-attempt-stopped-confirmation.json`은 프로세스 0·pid 부재를 기록합니다.
  이번 PC 관찰에서도 postgres 프로세스 0·해당 pid 부재를 확인했습니다.
- 현재 부분 클러스터는 논리 **9,499,955,169B**, 저장 파일 데이터 약 **3,687,800,801B**입니다.
  이 부분 결과는 삭제·교체하지 않습니다.

저장된 5개 RESULT의 경과 시간 합계는 **17,037.452초**입니다. 이는 여러 종료된 시도의
누적 관찰이며 성공 처리 시간도, 계속 실행 중인 예산도 아닙니다. 최신 종료 시도의
4시간 상한과 112.5초 사이의 차이 14,287.5초를 새 실행의 자동 허락으로 사용하지 않습니다.

재개를 검토할 때 유지할 상한은 **4시간·추가 6GiB·클러스터 논리 16GiB·절대 여유 4GiB**입니다.
현재 클러스터의 16GiB까지 논리 여유는 7,679,914,015B로 6GiB 추가 한도는 들어갑니다.
현재 C: 가용도 최소 10GiB 사전 조건은 충족하지만 동시 빌드·다른 쓰기가 이전 실패 원인이므로
자원을 분리해야 합니다. 진짜 재개 경계는 DB checkpoint와 실제 내용의 정확한 대조 후에 정합니다.

기존 dirty `local_restore.py`·`resume_restore.py`·관련 테스트 2개와 새 count-batches
테스트는 이 담당자가 수정하지 않았습니다. pending keyset count 개선은 통합 담당자가
검사·승격하며, frozen codec와 원본 manifest를 바꾸어 기존 checkpoint를 신뢰하지 않습니다.

## 5. 백업과 원본 물리 압축 판단

정기 백업의 마지막 상태는 10월 13일 04:20 KST `blocked_capacity`입니다.
필요 여유 20,256,899,302B, 당시 여유 5,177,217,024B였습니다. 부분 파일 정리 후보는 0개입니다.
고유 정기 dump 7개는 날짜별 복구 지점으로 보존합니다. `predeploy`·정기 보존 정책은 분리되어 있고,
부분 파일 부재를 정상 처리하는 `unlink(missing_ok=True)`·적용 직전 신원 재확인이 이미 있습니다.

서버의 offsite 영수증은 10월 9일 오후 dump의 암호화/복호화/독립 스키마·행 수 복원을
가리키며 재검사일은 10월 17일 KST, 만료일은 11월 9일 KST입니다. 최신 서버 변경분이나
compact 전행 내용 동일성을 검증한 기록이 아닙니다. PC 직접 streaming backup 도구는
기존 `ops/market/stream_backup.py`를 재사용할 수 있지만 이번에는 실행하지 않았습니다.

현재 원본 reader는 `.bin`, `.bin.gz`, 이전 JSON payload를 지원하고 원본 SHA를 검사합니다.
검증 reader는 중복 원본·legacy envelope의 비물리 메타데이터도 대조합니다. Toss·Naver·FnGuide의
압축 writer는 기본 지원하며 DART는 opt-in입니다. 따라서 조회 계약을 갈아엎기보다 기존
`compact_raw_archives.py`의 검증된 물리 보관 형식을 사용할 수 있습니다. 다만 아직 그 작업을
실행하지 않았고 실제 절감률·임시 공간·현재 reader/rollback 참조를 확정하지 않았습니다.

## 이번 종료 상태

**공간·전체 복원은 아직 해결되지 않았습니다.** 안전한 소규모 임시 정리 목록과
미검증 테스트 사본의 손실 없는 보존 도구를 마련했고 fixture를 통과했습니다. 실제 정리·
압축·완전 복원·현재 운영 변경분·정기 백업 정상화는 각각 별도 증거로 확인해야 합니다.
VPS 80% 조건 미달 동안 대량 과거 수집·색인은 재개하지 않습니다.

## 6. v138 공개 후 추가 확인 — 2026-10-13 21:54~21:59 KST

통합 담당자가 비활성 정적 tar **17개, 389,705,728B**를 실제 정리했습니다.
초기 후보의 명목 용량과 이 실제 정리 수치는 구분합니다. 이 감사 담당자는 추가 삭제를
하지 않았습니다. 당시 `current`는 `20261013-lucian-final-v138`였습니다.

- 통합 담당자의 21:54 `df`: 사용률 **98.14%**, 가용 **2,864,877,568B**,
  80% 미만까지 필요한 추가 회수 **28,097,182,926B**입니다.
- 감사 담당자의 직후 `df`: 가용 2,864,451,584B입니다. 조회 사이 쓰기 증가를 보존하며
  앞선 수치를 최신값으로 덮어쓰거나 원인을 단정하지 않습니다.
- 초기 감사와 비교한 `incoming`은 320,143,360B 감소, `releases`는 710,455,296B 증가,
  `backups`는 12,288B 증가했습니다. 세 경로를 제외한 약 **1.58GB의 증가는 미확정**입니다.
  원본·DB·다른 서비스의 전수 스캔을 수행하지 않았습니다.
- 최신 세 배포본에는 기존 web runtime·wheels·public·이전 정적 화면이 함께 승계됐습니다.
  **현재 v138·최근 정상 v137/v136·v131 호환** 참조를 보호합니다. 하드링크와 롤백 관계를
  확인하기 전에는 각각의 `du` 합계를 회수 가능량으로 계산하지 않습니다.
- 알려진 web 이미지 14개를 작은 inspect 묶음으로 읽고 전체 27개 컨테이너 Image ID와
  대조했습니다. 과거 13개 이미지의 60태그에 컨테이너 참조는 없지만 모든 RootFS 레이어가
  보호한 v93 fallback 이미지의 레이어 부분집합입니다. **보호 레이어를 유지한 회수량은 0B**입니다.
  태그·설정 메타데이터의 작은 절감량은 측정하지 않았습니다. 같은 prefix 목록 시간초과
  명령은 세 번째 재시도하지 않았으며 전체 Docker prune도 수행하지 않았습니다.

ignored 증거 파일의 SHA-256:

| 증거 | SHA-256 |
|---|---|
| [이미지·컨테이너 참조](../../../work/lucian-v136/docker-v138-readonly.json) | `4837bb6bd641ce961e166d65f5077a81104bf67cefc5957c20f5cf886743d3de` |
| [제한된 경로별 용량](../../../work/lucian-v136/storage-v138-components.json) | `139b038c546e2fea491461b3f6c1225f2425fb4aaecd81781dcb6807c802a8f5` |

증거에는 이미지 ID·태그·RootFS 레이어·소유 경로의 용량·활성 참조만 있으며 환경변수나
인증값은 포함하지 않습니다. 자세한 제한은 `STORAGE_REGROWTH_REVIEW.md`에 기록했습니다.
새로 확정한 대용량 삭제 대상은 없고 **저장공간·PC 전체 복원 RESULT는 아직 미통과**입니다.
PC 독립 복원은 통합 담당자 작업으로 계속 실행 중이므로 이 담당자가 새 복원·대형 빌드·
중복 압축·삭제를 실행하지 않았습니다. 용량 부족 상태에서 대량 과거 수집을 재개하지 않습니다.

### 마지막 제한 메타 확인 — 22:04:55 KST

기존 DB 컨테이너 안의 설정을 값 출력 없이 사용해, statement timeout 5초·read-only로
`pg_database_size`와 `pg_stat_wal`만 조회했습니다. 테이블 스캔·행 수 집계는 하지 않았습니다.
DB 크기는 **41,532,614,323B**로 이전 41,530,951,347B보다 **1,662,976B** 증가했습니다.
따라서 DB 본체 크기 증가는 앞의 약 1.58GB 재증가를 설명하지 못합니다.

`pg_stat_wal.wal_bytes=128,866,732,847B`는 10월 8일 통계 초기화 이후의 누적 생성량입니다.
현재 WAL의 디스크 용량이나 이번 증가분으로 사용하지 않습니다. 정확한 활성 원본 `raw`
경로의 `du`는 3초 한도에 걸려 종료했고 같은 조회를 반복하지 않았습니다. 남은 증가 원인은
미확정입니다. 신규 배포 버전의 상태를 이 v138 관찰 기록에 소급해서 붙이지 않습니다.

추가 ignored 증거는 [DB 메타 조회](../../../work/lucian-v136/storage-v138-db-metadata.json)이며
SHA-256은 `bed9712368c5dd0776ccfbbadcd05c0d5116367d23292904f9b3577ce84f1285`입니다.
인증값·쿼리 오류 원문 없이 용량·통계 기준 시각·한도 중단만 기록했습니다.
원본·DB·서비스 변경은 없습니다.

22:07:03 KST에 별도 메타 쿼리 한 번으로 실제 WAL 파일을 확인했습니다.
`pg_ls_waldir`는 **20개·335,544,320B(320MiB)**, `max_wal_size=1GB`,
`wal_keep_size=0`입니다. 복제 슬롯 0개·슬롯 보존량 합계 0B, 다른 열린 트랜잭션 0개였습니다.
현재 WAL 크기도 약 1.58GB 증가를 설명하기에는 작습니다. 이전 WAL 크기 기준선은
없으므로 정확한 WAL 변화량을 계산하지 않습니다. 이 조회도 read-only·5초 제한이었으며
슬롯명·다른 세션의 쿼리·인증값은 출력하지 않았습니다. CHECKPOINT·설정 변경·슬롯 삭제·
WAL 삭제·VACUUM은 실행하지 않았고 이후 추가 조사 없이 종료합니다.

ignored 증거: [WAL 메타 조회](../../../work/lucian-v136/storage-wal-metadata-20261013.json).
SHA-256: `d6e51822310f3b45b0b6943f1b739b00dd1af6fa2124ea3da584bdb6c9a8763e`.
