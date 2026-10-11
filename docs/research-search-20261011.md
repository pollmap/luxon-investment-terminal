# 근거 문서 검색 계약 · 2026-10-11

검색 결과는 저장된 문서의 제목 또는 원문 구절입니다. 답변 생성, 외부 URL 수집, 뉴스 본문 제공을 하지 않습니다. 기존 `market_context_documents`가 있으면 새 색인·모델·DDL 없이 제목을 검색합니다.

## 공개 연결

앱 구성 시 한 번 `research_search.attach_routes(app, BASE, get_store)`를 호출합니다.

- `GET /api/market/v1/research/search?q=반도체&code=005930&kind=news&from=2026-01-01&to=2026-10-11&limit=10`
- `GET /api/market/v1/research/documents/{64자리 id}?revision={64자리 revision}`

`q`는 1~200자, `limit`은 기본 10·최대 20입니다. `code`, `kind`, `from`, `to`는 선택 조건입니다. `kind`는 `news/filing/ir/evidence`입니다. 문서의 이전 revision을 요청하면 다른 revision으로 대체하지 않습니다. 결과·오류는 공유 캐시 대상에서 제외합니다.

아래 값은 **API 모양을 설명하는 fixture**입니다. 운영 확보율이나 실제 기사라고 표시하지 않습니다.

```json
{
  "status": "ready",
  "mode": "lexical_only",
  "fallback_reason": "embedding_not_configured",
  "available_documents": 1,
  "items": [{
    "id": "64자리 document SHA",
    "chunk_id": "64자리 chunk SHA",
    "title": "반도체 공급 계약",
    "excerpt": "반도체 공급 계약",
    "code": "005930",
    "company": null,
    "kind": "news",
    "source_url": "https://example.com/article/fixture",
    "published_at": "2026-10-01",
    "section": "제목",
    "page": null,
    "revision": "64자리 revision SHA",
    "source_hash": "원문 SHA 또는 null",
    "source_hash_status": "present",
    "index_version": "context_titles_only"
  }],
  "coverage": {
    "index": "context_titles_only",
    "candidate_limit": 1000,
    "truncated": false,
    "ranking_scope": "bounded_collected_documents"
  }
}
```

`available_documents`는 회사·종류·기간에 맞는 수집 문서 수입니다. 검색어 일치 수나 전체 국내 기업 자료 확보율이 아닙니다. 제목 fallback의 `company`는 없을 수 있습니다. `source_hash_status=present`는 해시 필드가 있다는 뜻이며 원문 검수 완료를 뜻하지 않습니다. `coverage.truncated`가 참이면 후보·시간·응답 크기 제한으로 일부만 확인한 결과입니다.

## 색인과 원본

`ResearchRepository.initialize()`와 `ingest(...)`/`project_context(after=...,limit=...)`는 별도 수집 작업에서 명시적으로 호출합니다. 공개 GET은 이 메서드를 부르지 않습니다. 기존 원본과 문서 revision은 삭제하지 않습니다. 색인은 append-only 청크와 현재 head를 분리합니다. 같은 document/revision/index version에 다른 내용이 들어오면 거절합니다.

`ingest` 입력은 document_id, revision, kind, title, url, published_at, issuer, source_hash와 sections입니다. 본문 sections가 있으면 원문 SHA가 필요합니다. 각 section은 `text/section/page` 또는 `kind=table/header/rows/section/page`입니다. 표는 원래 행 단위로 나누고 긴 행만 추가 분할합니다. 반복한 표 머리글과 본문 오프셋을 따로 보존합니다.

모델 tokenizer를 주면 실제 token offset으로 320토큰·48겹침을 적용합니다. 모델이 없으면 `lexical_only:unicode-word-v1:320:48` 버전으로 분리합니다. 후속 모델 색인은 모델·tokenizer SHA가 포함된 새 버전으로 생성하여 기존 청크를 덮지 않습니다. 뉴스에는 항상 제목만 색인합니다.

## 임베딩 선택 연결

공개 API는 기본적으로 모델을 메모리에 로드하지 않습니다. `MARKET_RESEARCH_EMBEDDING_URL=http://research-embedder:8190`과 기존 `MARKET_INTERNAL_TOKEN`, 아래 SHA 변수 2개가 있을 때 내부 클라이언트만 만듭니다. 다른 host·포트·경로는 거절합니다. 모델 서비스의 자원 상한과 Docker 연결은 별도 배포 설정입니다.

내부 HTTP 계약은 Bearer 토큰과 POST `/v1/embed`의 `{text,query}`, `/v1/tokenize`의 `{text}`입니다. 응답은 항상 `model_version`과 `dimensions:384`, 각각 `vector` 또는 `offsets`를 포함합니다. model_version은 `e5-small-int8-384:{model_sha256}:{tokenizer_sha256}`입니다. tokenize는 special token 없는 원문 char offset을 반환합니다. 0.6초 호출 제한, 동시 실행 1개, 실패 시 5~40초 재시도 대기, 최대1MiB 응답을 적용합니다. 타임아웃이 나도 아직 끝나지 않은 HTTP 작업은 1개 슬롯을 계속 점유하며 추가 작업을 쌓지 않습니다. redirect·프록시 환경변수·압축 응답을 사용하지 않습니다.

로컬 개발 검증은 `MARKET_RESEARCH_LOCAL_EMBEDDING=1`을 명시하고 아래 환경변수 **4개가 모두 있을 때만** 로컬 파일을 한 번 로드합니다.

| 변수 | 값 |
|---|---|
| `MARKET_RESEARCH_MODEL_ONNX` | 검증된 로컬 E5-small INT8 ONNX 경로 |
| `MARKET_RESEARCH_TOKENIZER_JSON` | 같은 모델의 tokenizer.json 경로 |
| `MARKET_RESEARCH_MODEL_SHA256` | ONNX 실제 SHA256 |
| `MARKET_RESEARCH_TOKENIZER_SHA256` | tokenizer 실제 SHA256 |

별도 모델 서비스와 로컬 검증의 선택 의존성은 `onnxruntime`, `tokenizers`, `numpy`입니다. 내부 클라이언트는 기존 `httpx`만 사용합니다. 요청 중 설치·다운로드하지 않습니다. 모델 파일 최대 512MiB, tokenizer 최대 32MiB이며 해시가 다르면 사용하지 않습니다. 변환자는 CPU-neutral INT8 산출물과 모델 출처/변환 옵션을 별도 manifest로 보존해야 합니다. 어댑터는 파일 해시·입출력·CPU provider를 검증하며 INT8 변환 자체를 증명하는 도구는 아닙니다.

계약은 `intfloat/multilingual-e5-small`, 입력 `query: `/`passage: `, 최대 512토큰(초과 시 거절), `[1,tokens,384]` 출력, attention-mask mean pooling, L2 정규화입니다. CPU provider·연산 스레드 1개·동시 추론 1개·기본 250ms native 종료 요청을 사용합니다. 입력 tokenization 및 세션 초기 로드는 이 추론 제한과 별개입니다.

`load_from_env()`는 `(adapter, fallback_reason)`을 반환합니다. 미설정·설정 일부 누락·파일 문제·해시 불일치·runtime 미설치·로드 실패를 고정 코드로 구분하며 파일 경로·예외 원문을 공개하지 않습니다. 요청 중 임베딩이 실패해도 키워드 결과를 제공합니다. 정상 벡터와 어휘 순위는 RRF `1/(60+rank)`로 결합합니다.

## PostgreSQL 선택 migration

`services/market_dashboard/research_search.sql`은 **운영에서 아직 실행하지 않은 명시적 migration**입니다. PostgreSQL에 호환 pgvector 바이너리를 먼저 설치하고 디스크 여유·백업·롤백을 확인해야 합니다. 현재 공간 압박 중에는 실행하지 않습니다.

SQL은 pg_trgm, vector(384), 신규 청크의 JSON 벡터→vector 열 trigger, 제목/본문 GIN, HNSW를 추가합니다. 기존 전체 행 backfill은 자동 실행하지 않습니다. 색인 생성 실패 시 `pg_index.indisvalid`를 확인해야 합니다. SQL 검색은 회사·종류·기간과 키워드/trigram 조건을 후보 LIMIT 전에 적용합니다. pgvector가 있으면 동일 조건의 nearest 60개를 추가해 RRF를 적용합니다. ANN의 필터 적용으로 결과 수·재현율이 제한될 수 있으며 전체 문서 검색 완전성을 보장하지 않습니다.

SQLite에는 키워드/3글자 후보 SQL 필터와 결정론적 Python 순위를 사용합니다. SQLite 벡터 비교는 개발·fixture용 제한 후보 내 연산이며 전체 ANN과 동일하지 않습니다.

## 보호와 검증 범위

- 프로세스당 120회/분, 동시 검색 4개. 최대 20개가 750ms까지 슬롯을 기다리며 추가 초과는 429와 Retry-After 1초입니다. 대기는 전역 요청 예산 잠금 밖에서 수행합니다.
- PG 각 문장 1.5초 제한, SQLite progress timeout, 어휘 순위 계산 750ms 상한.
- 문서 입력 2MiB, 청크 320토큰/24,000자, 문서 5,000청크, 검색 최대20결과, 문서 상세 최대100청크/약1MiB.
- 공개 URL만 링크로 반환하며 사용자 URL을 가져오지 않습니다. 내부 host·인증정보·비HTTP 프로토콜을 거절합니다.
- 모델 없음, 제목 fallback, 필터, 이전 revision, 불변 청크, 실제 tokenizer 오프셋, 뉴스 본문 제외, SQL 입력 바인딩, GET 비쓰기와 로드 실패를 SQLite/순수 fixture로 검증합니다.
- PostgreSQL 확장 실제 설치·대량 색인·실제 모델 정확도·공개 서비스 지연은 별도 검증입니다. fixture 통과를 이 항목의 완료로 쓰지 않습니다.

공식 계약: [pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html), [pgvector](https://github.com/pgvector/pgvector), [E5-small 모델](https://huggingface.co/intfloat/multilingual-e5-small).
