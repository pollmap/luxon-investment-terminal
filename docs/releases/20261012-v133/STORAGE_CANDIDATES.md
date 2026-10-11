# 2026-10-12 저장공간 읽기 점검

삭제·복원·압축·배포를 실행하지 않은 조사입니다. 실행 전 현재 참조와 여유 공간을 다시 확인해야 합니다.

## PC

`apps/web`의 오래된 개발 시험 산출물 세 곳을 Windows `GetFileInformationByHandleEx(FileStandardInfo)`로 읽었습니다. 파일의 `AllocationSize`와 `NumberOfLinks`를 확인했고, 재파스 지점은 따라가지 않았습니다.

| 저장소 기준 경로 | 단독 링크 실제 할당 바이트 | 파일 수 | 참조·상태 |
|---|---:|---:|---|
| `apps/web/.next-chart-parallel-v102` | 92,460,664 | 244 | 해당 별칭을 포함한 실행 명령 0건 |
| `apps/web/.next-news-boundary-smoke` | 108,055,352 | 99 | 해당 별칭을 포함한 실행 명령 0건 |
| `apps/web/.next-search-intent-smoke` | 78,262,592 | 204 | 해당 별칭을 포함한 실행 명령 0건 |
| 합계 | 278,778,608 | 547 | 측정 오류·하드링크·재파스 0건 |

확인한 node/python 프로세스 78개 모두 명령행을 읽을 수 있었으며, 원문 명령행은 기록하지 않았습니다. 이는 정확한 별칭 참조 검사이며 모든 종류의 파일 핸들 검사는 아닙니다.

통합 담당자의 이 세 경로 삭제 명령은 자동 승인 검토에서 `blocked by policy`로 거부됐습니다. 실행된 삭제는 없으며, 다른 셸·이름 변경·파일별 삭제로 우회하지 않습니다. 따라서 이 용량은 **실제 확보 공간에 포함하지 않습니다.** 이전에 거부된 `x20/work/stock-master-security/node_modules`도 조사·삭제 후보에서 제외했습니다.

`work/build`는 비어 있습니다. `work/releases`는 버전별 하위 디렉터리만 있고 바로 아래 대형 아카이브는 없었습니다. `.next-v76`은 30초 제한에 도달해 완전 측정하지 못했습니다. 확인한 24,812,480바이트를 전체 크기나 확보 가능량으로 사용하지 않습니다. 기본 `.next`는 현재 빌드와 혼동할 수 있어 제거 후보에서 제외했습니다.

처음 수행한 `GetCompressedFileSizeW` 반올림 측정과 `DirEntry.stat().st_nlink`는 물리 회수량을 확정하는 데 사용하지 않습니다. 최종 근거는 `pc-fixture-physical.json`의 핸들 기반 측정입니다.

## VPS

측정 시 current는 `/srv/services/korea-stocks/releases/20261011-stock-shopping-v132`였습니다. 루트 파일시스템 사용량은 146,160,902,144바이트, 여유는 8,716,509,184바이트로 95%였습니다. 수집 등으로 수치는 계속 바뀔 수 있습니다.

| 경로 | 할당 바이트 | 판단 |
|---|---:|---|
| `/srv/services/korea-stocks/releases/20261010-share-review-v113` | 236,298,240 | 통합 담당자 검토용 비활성 배포 후보 |
| `/srv/services/korea-stocks/releases/20261010-ocf-republication-v112` | 236,232,704 | 통합 담당자 검토용 비활성 배포 후보 |
| `/srv/services/korea-stocks/releases/20261010-storage-writes-v110` | 236,236,800 | 통합 담당자 검토용 비활성 배포 후보 |
| 합계 | 708,767,744 | 삭제 미실행 |

`du -s -B1`에 보존 대상 v132·v131·v130을 먼저, 후보들을 뒤에 전달해 해당 집합 안의 하드링크 중복을 제외했습니다. 루트 파일시스템의 실제 해제량은 삭제 후 `df` 차이로 별도 확인해야 합니다. 다른 경로의 하드링크·열린 파일까지 전수 조사한 값은 아닙니다.

현재 API·worker의 compose 작업 경로는 v132, 기존 web 컨테이너는 v93, OpenAlgo는 v73입니다. 검사한 컨테이너 mount는 공유 원본·인증·OpenAlgo 데이터이며 위 후보를 가리키지 않습니다. 보존 3개 배포본의 최상위 JSON/YAML에도 세 후보의 직접 참조가 없었습니다. Nginx root는 `/var/www/korea-stocks/current`이며 실제 경로는 `/var/www/korea-stocks/releases/20261011-stock-shopping-v132-20261011T132633345074Z`로, 후보와 분리됩니다. 후보의 내용은 앱·런타임·wheel·정적 화면 사본 및 배포 기록이며, DB·원본 저장 디렉터리는 포함되지 않았습니다. 삭제 직전 최종 실행 참조를 다시 대조해야 합니다.

v129(236,593,152바이트)와 v128(236,396,544바이트)은 보존 배포본의 과거 배포 기록에 참조되어 있어 위 확정 후보 합계에서 제외했습니다. v130의 `deployment.json`에 v129, 보존본의 `cache-lock-tuning.json`에 v128이 등장합니다. 이 기록이 실행 의존성인지 단순 이력인지 별도 판정 전에는 보존합니다.

v129·v128 API 이미지의 RootFS 레이어는 보존 v132·v131·v131 호환 롤백·v130 이미지 레이어에 모두 포함됩니다. 따라서 이 두 이미지의 표기 크기 약 161MB씩을 회수 가능 공간으로 계산하면 안 됩니다. 이번 조사에서 확인한 **추가 고유 이미지 레이어는 0개**입니다.

`/tmp/v05-market-data.tar.gz`는 486,658,048바이트를 할당하지만 데이터 원본 가능성이 있어 보존합니다. 소규모 과거 패치 압축파일은 발견했으나 근거 대비 회수 효과가 작아 이번 실행 후보에 추가하지 않았습니다.

## 보존과 한계

- 현재 배포·최근 정상 2개·호환 롤백·reader floor·기존 web/OpenAlgo 참조는 보존합니다.
- DB·원본·백업·독립 복원 입력 아카이브·부분 복원 클러스터는 이 점검의 삭제 대상이 아닙니다.
- 전역 Docker 캐시·volume·다른 프로젝트는 변경하지 않습니다.
- `docker image ls`는 20초 타임아웃이 발생해 중단했습니다. 이후 지정된 6개 이미지의 선택 필드만 조회했습니다. 전역 `docker system df -v`를 재실행하지 않았습니다.
- 현재 확인한 안전 후보만으로 VPS를 80% 미만으로 낮추거나 PC 복원 예산을 충족한다고 보장할 수 없습니다. 독립 복원 검증과 원본 보관 이전이 별도로 필요합니다.

## 근거

저장소의 `work/operations-v133/`:

- `pc-fixture-physical.json`: 최종 파일 핸들 기반 할당 측정
- `pc-fixture-process-refs.json`: 비밀값 없는 정확 별칭 참조 수
- `pc-cache-candidates.json`: 초기 참고 측정, 최종 회수량 근거에서 제외
- `vps-targeted-candidates.json`: 지정 이미지의 공유 레이어 및 배포본 할당
- `vps-release-refs.json`: current·compose·배포 기록 참조
- `vps-old-release-content.json`: 후보 세 배포본의 최상위 구성과 할당
- `vps-nginx-refs.json`: Nginx의 서비스 경로 및 후보 참조 검사
- `vps-nginx-filesystem-refs.json`: 실제 정적 root와 심볼릭 링크 해석 결과
- `cleanup-candidate-manifest.json`: 실행 담당자가 검토할 후보·차단·보존 목록

## 후속: 중단된 별도 PostgreSQL의 압축 가능성

03:13 KST에 PostgreSQL 프로세스 0개를 확인했습니다. 지정된 다섯 클러스터의 `PG_VERSION`은 모두 17이며 `postmaster.pid`가 없고 외부 테이블스페이스 링크도 없었습니다. `restore-daily-20261010-v106`, `pg-delta-candidate` 두 실행, `collection-merge-pg`, `storage-v105/pg-sample-study`의 `base/<database>` 바로 아래 파일 메타데이터만 읽었습니다.

- `restore-daily-20261010-v106`에서 확인한 100MiB 이상 데이터 파일은 모두 이미 NTFS 압축 상태였고 링크 수는 1입니다. 예를 들어 `base/16384/17168`은 논리 1,073,741,824바이트·할당 647,479,296바이트, `base/16384/65536`은 논리 1,073,741,824바이트·할당 444,952,576바이트입니다.
- 나머지 네 클러스터에는 100MiB 이상 데이터 파일이 없었습니다. 새 대형 미압축 데이터 파일 후보는 발견하지 못했습니다.
- 별도로 첫 클러스터의 `pg_wal`에 미압축 16MiB 파일 31개, 논리 합계 520,093,696바이트가 있습니다. 나머지 네 클러스터의 미압축 WAL은 논리 합계 100,663,296바이트입니다. 이는 실제 압축 회수량이 아니며, 전부 압축해도 당시 부족분 약 2.25GB를 해결할 수 없습니다.
- 과거 `verification.json`과 fixture RESULT에서 독립 복원·시험 소유를 확인했습니다. 과거 완료 표시는 이번 전체 복원 통과 증거로 사용하지 않습니다.
- 파일 내용·해시는 읽지 않았고, 압축·삭제·PostgreSQL 시작을 실행하지 않았습니다. 차단된 fixture에는 접근하지 않았습니다.

근거는 `work/operations-v133/pc-stopped-pg-compression-options.json`과 `pc-stopped-pg-wal-options.json`입니다.

## 추가: 별도 임시 빌드 패키지

`work/releases`와 `work/storage-v*`는 깊이 2까지 파일 메타데이터만 확인했습니다. `runs`, `cluster-data`, `data`, `raw`, `node_modules`, `.git`은 탐색하지 않았습니다. 10MiB 이상 파일 25개 중 대부분은 약 23MB 정적 빌드 패키지였습니다. 현재·최근 정상 2개 배포 패키지와 복원 결과는 후보에서 제외했습니다.

다음 세 파일은 각각 실제 할당 22,933,504바이트·링크 수 1입니다. 압축을 풀지 않고 tar 멤버 이름을 순차 확인했으며 각각 정적 파일 74개, 심볼릭/하드링크 0개, DB·raw·원본 코드·node_modules 경로 0개였습니다. 03:19 KST에 확인한 관련 프로세스 73개에서 파일명 참조는 0건입니다. 실행 담당자 검토용이며 삭제·압축은 하지 않았습니다.

- `work/storage-v117/v117-search-index-static-web.tar.gz`
- `work/storage-v117/v118-rank-status-static-web.tar.gz`
- `work/storage-v117/v119-rank-status-static-web.tar.gz`

합계는 **68,800,512바이트**로 추가 약 2.3GB 확보 목표에 못 미칩니다. `apps/web/.next-v76`은 파일 핸들을 전부 열지 않는 경량 stat으로 끝까지 확인했으며, 재파스 27개를 따라가지 않은 실제 내부 파일은 1,746개·논리 30,642,548바이트였습니다. 이 폴더 역시 대형 후보가 아니며, node_modules나 재파스 대상은 제거 후보로 삼지 않습니다.

근거: `pc-shallow-package-inventory.json`, `pc-static-package-candidates.json`, `pc-static-package-process-refs.json`, `pc-v76-large-build-files.json`.

## 추가: VPS의 남은 빌드 범위

통합 담당자가 구배포 일부 컴파일 사본을 정리한 뒤, 주식 서비스의 지정된 경로만 다시 읽었습니다.

| 경로 | 확인한 개수 | 전체 할당 바이트 | 보존 주의 |
|---|---:|---:|---|
| `/srv/services/korea-stocks/releases` | 11개 배포 디렉터리 | 1,951,330,304 | 현재·최근2개·reader floor·web·OpenAlgo 포함 |
| `/srv/services/korea-stocks/incoming` | 파일·소규모 작업 디렉터리 | 676,540,416 | 현재 패키지·소스·자료 수입 작업 포함 |
| `/var/www/korea-stocks/releases` | 4개 정적 배포 | 98,635,776 | 현재와 최근2개 포함 |
| `/tmp`의 기존 주식 빌드 패키지 | 49개 파일 | 24,809,472 | 개별 내용·참조 미검증 |

위 숫자는 각 디렉터리 내부에서 중복 하드링크를 제외한 전체 할당량이며, 전부 회수할 수 있는 용량이 아닙니다. 보호 대상이 섞여 있으므로 합계를 삭제 목표로 사용하면 안 됩니다. 현재 정적 링크는 계속 v132를 가리킵니다. 이 좁은 범위에서는 **추가 2GB 이상을 안전하게 회수할 수 있는 비활성 컴파일 사본을 확인하지 못했습니다.** 더 넓은 원본·DB·백업 탐색이나 Docker 정리는 하지 않았습니다.

근거: `work/operations-v133/vps-large-build-followup.json`.
