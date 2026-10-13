# braces 보안 경고와 내장 glob 대체 검토

2026-10-13 KST. 저장소 `pollmap/luxon-investment-terminal`, 현재 제품 브랜치와 원격 `master`를 읽기 전용으로 확인했습니다. 이 기록은 의존성 경고 27번에 한정합니다.

## 판단

**경고는 미해결로 유지합니다.** 공식 수정 버전이 없으며, 내장 glob 대체 후보는 호환성 검사에서 61개 중 60개만 통과했습니다. 실패하는 설정을 알고도 설치·lockfile·ESLint 설정을 변경하거나 경고를 해제하지 않았습니다. 이 경고를 해결했다고 표현하는 보안 PR도 만들지 않았습니다.

| 확인 | 결과 |
|---|---|
| 공식 권고 | `GHSA-vfj7-8cjw-p6xm`, `CVE-2026-93687`, 취약 범위 `<=3.0.3`, 수정 버전 없음 |
| 실제 GitHub 경고 | `#27`, `open`, manifest `pnpm-lock.yaml`, 자동 분류 `scope=runtime` |
| 현재 설치 그래프 | `eslint-config-next16.3.8 → @next/eslint-plugin-next16.3.8 → fast-glob3.3.1 → micromatch4.0.8 → braces3.0.3` |
| 실제 사용 구분 | 위 체인은 웹의 `devDependencies`에서 들어옵니다. `pnpm why braces --prod --json` 결과는 `[]`입니다. GitHub의 lockfile 분류와 이 설치 그래프의 구분을 함께 기록합니다. |
| 실제 코드 소비 | Next ESLint 플러그인의 `dist/utils/get-root-dirs.js` 한 곳이 `fast-glob`을 가져옵니다. 입력은 ESLint `settings.next.rootDir`입니다. |
| 현재 설정 | `apps/web/eslint.config.mjs`에 사용자 지정 `settings.next.rootDir`가 없습니다. 기본 분기는 `[context.cwd]`이며 glob 함수를 호출하지 않습니다. 모듈의 `require` 자체는 실행하므로 의존성만 제거하면 lint가 깨집니다. |
| 공개 서비스와의 관계 | 작성한 앱·시장 API·Cloudflare 코드에서 이 체인의 사용 또는 공개 요청을 `rootDir`에 전달하는 경로는 발견하지 못했습니다. 정적 배포 스크립트는 컴파일된 `apps/web/out`을 배포하며 개발용 `node_modules`를 게시하지 않습니다. 모든 상위 라이브러리 내부까지 공개 노출이 0건이라고 전수 증명한 결과는 아닙니다. |

공식 권고의 내용은 깊게 중첩된 중괄호 입력에 따른 재귀 처리의 스택 고갈입니다. 업스트림 이슈에는 적용 범위에 대한 반론도 있지만, 공식 권고와 경고가 열린 상태이므로 이를 해제 근거로 사용하지 않습니다. [GitHub 권고](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), [업스트림 이슈](https://github.com/micromatch/braces/issues/70)

## 대체 후보의 실제 검사

Node `v24.18.0`, 플러그인 `16.3.8`, 기존 `fast-glob3.3.1`을 기준으로 설치 패키지 밖의 임시 후보를 검사했습니다. 원본 플러그인의 `getRootDirs`를 실제 기준 함수로 호출하고 후보만 VM으로 읽었습니다. 후보에서 `node:fs` 이외의 import는 허용하지 않았습니다. 금융 자료를 읽거나 설치 패키지를 바꾸지 않았습니다.

| 검사 항목 | 결과 |
|---|---|
| 기본 cwd, 빈 배열, 여러 rootDir, 비문자 배열 항목, 중복 rootDir | 통과 |
| 정적 디렉터리, 파일 제외, 없는 디렉터리, 빈 패턴 오류 | 통과 |
| `./` 접두사, 후행 슬래시, Windows 구분자, 절대 경로 | 통과 |
| `**`, `/**`, 중첩 재귀, 디렉터리 전용 필터 | 통과 |
| 중괄호 목록·범위·중첩, 문자 클래스, 확장 glob, 그룹 | 검사 대상에서 통과 |
| 디렉터리 링크, 링크 아래 재귀, 없는 링크 대상 | 통과 |
| `packages/!(a)` | **실패:** 기존 결과에는 `packages/.hidden`이 포함되지만 내장 glob 후보에는 없습니다. |

출력 순서는 기존 fast-glob도 계약상 임의 순서이며 실제 소비자는 모든 경로의 페이지 목록을 합칩니다. 따라서 디렉터리의 집합과 중복 횟수를 비교했습니다. rootDir 배열의 각 항목을 별도로 처리하는 기존 동작은 유지했습니다. 위 61개 검사는 전체 glob 문법을 전수 증명하는 것은 아닙니다. [fast-glob 출력 계약](https://github.com/mrmlnc/fast-glob#readme)

첫 단순 조사에서 발견한 `.`·`./`·끝의 `/**`·그룹 차이는 후보에서 보정했습니다. 남은 부정 확장 glob의 숨김 디렉터리 차이를 자체 glob 엔진으로 확대 구현하지 않고, 호환성 실패가 확인된 첫 후보 검사로 종료했습니다. 현재 기본 cwd만 대상으로 의존성을 제거하는 제한적 패치는 가능하지만 사용자 지정 rootDir의 기존 계약을 줄이므로 채택하지 않았습니다.

- 명령: `node work/security-braces/native-glob-comparison.mjs`
- 종료 코드: **1**
- 총 검사: **61**, 통과 **60**, 실패 **1**
- 원본 `get-root-dirs.js` SHA-256: `886677432990a735e5ebdfb345ff1cbd40e264f9947a8432eeeb254b3a926bde`
- 후보 SHA-256: `41f2777b2b1deaef937cd07ce4d5caed0abe40ff5138cc7baf1a2b19456f4d36`
- 상세 결과: ignored `work/security-braces/comparison.json`
- 임시 후보: ignored `work/security-braces/candidate-get-root-dirs.js`
- 설치 패키지·pnpm 설정·lockfile 변경: **없음**

`fs.globSync`는 Node24에서 안정화됐지만 디렉터리 링크를 따르는 `followSymlinks`는 24.16부터 추가됐습니다. 새 코드의 기반을 Node24 전체로 표현하면 안 됩니다. 현재 로컬은 24.18, CI는 최신 24 계열이지만 대체안 자체는 채택하지 않았습니다. [Node24 공식 명세](https://nodejs.org/docs/latest-v24.x/api/fs.html#fsglobsyncpattern-options)

## 업스트림과 기본 브랜치

공식 registry와 Next canary를 읽은 결과, 단순 버전 상승으로 체인이 사라지지 않았습니다. 확인한 버전은 `@next/eslint-plugin-next16.4.0` 및 `16.5.0-canary.9 → fast-glob3.3.1`, `fast-glob3.3.3 → micromatch^4.0.8`, `micromatch4.0.8 → braces^3.0.3`, `braces3.0.3`입니다. 새 버전 설치나 canary 채택은 하지 않았습니다.

실제 원격 `pollmap/luxon-investment-terminal/master`는 `eslint-config-next16.3.8`과 Node24 CI를 사용합니다. 다음 파일을 `gh api`로 읽었으며 다른 저장소의 파일을 판단 근거로 사용하지 않습니다.

| 원격 파일 | Git blob SHA |
|---|---|
| `apps/web/package.json` | `c5a8053a34dcd785e73b9ac2bd2b74973b6a8b91` |
| `pnpm-workspace.yaml` | `7f3ed8f6c04435a286c35e665aa37d7f3ae01575` |
| `.github/workflows/ci.yml` | `0414d7dea6609dc7a74b389432e0a7576b6abc3b` |

pnpm10 공식 문서에서 버전 고정 `patchedDependencies`와 부모 의존성에 한정한 `"@next/eslint-plugin-next@16.3.8>fast-glob": "-"` 제거 문법을 확인했습니다. 그러나 패치 호환성이 통과하지 않았으므로 두 설정을 적용하지 않았습니다. 후속 공식 수정 또는 대체 구현이 나왔을 때 기존 설정·lint를 다시 검증한 뒤 경고 상태를 확인해야 합니다. [pnpm10 패치](https://github.com/pnpm/pnpm.io/blob/main/versioned_docs/version-10.x/cli/patch.md), [의존성 제거](https://pnpm.io/settings/dependency-resolution#overrides)

현재 공개 정적 배포를 위한 개발 도구의 미해결 경고라는 점과, 전체 보안 검사 통과가 아니라는 점을 함께 유지합니다. 일반 금융 조회 요청에서 취약 입력 경로를 발견하지 못했다는 사실을 업스트림 취약점 자체의 해결로 바꾸지 않습니다.
