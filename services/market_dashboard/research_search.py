"""Evidence retrieval, never answer generation. Public requests are read-only.

The optional chunk projection is collector-owned. Until it is populated, stored
context titles remain searchable without provider calls or schema creation.
"""

import ipaddress
import json
import re
import threading
import time
import unicodedata
from collections import deque
from contextlib import contextmanager
from datetime import date
from urllib.parse import urlsplit

from sqlalchemy import (
    JSON,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    inspect,
    literal_column,
    or_,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError

from .market_context import (
    DOCUMENTS,
    LINKS,
    ContextRepository,
    day_of,
    digest,
    encoded,
    insert_ignore,
    public_url,
    securities,
    verified_document_codes,
    verified_document_filter,
)
from .research_embeddings import EmbeddingUnavailable, load_from_env, normalized_vector
from .store import now

VERSION = "research-search-v1"
ASSOCIATION_VERSION = ":associations-v2"
LEXICAL_VERSION = "lexical_only:unicode-word-v1:320:48" + ASSOCIATION_VERSION
KINDS = {"news", "filing", "ir", "evidence"}
META = MetaData()
HEADS = Table(
    "market_research_heads",
    META,
    Column("document_id", String(64), primary_key=True),
    Column("revision", String(64), nullable=False),
    Column("index_version", String(200), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("published_day", String(10)),
    Column("recorded_at", String(40), nullable=False),
)
CHUNKS = Table(
    "market_research_chunks",
    META,
    Column("chunk_id", String(64), primary_key=True),
    Column("document_id", String(64), nullable=False),
    Column("revision", String(64), nullable=False),
    Column("index_version", String(200), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("search_text", Text, nullable=False),
    Column("metadata", Text, nullable=False),
    Column("embedding", Text),
    Column("embedding_version", String(200)),
)
Index(
    "market_research_chunk_document_idx",
    CHUNKS.c.document_id,
    CHUNKS.c.revision,
    CHUNKS.c.index_version,
)
Index("market_research_head_filter_idx", HEADS.c.kind, HEADS.c.published_day)
SHA = re.compile(r"[a-f0-9]{64}\Z")
TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def evidence_url(value):
    """Only public browser links. This module never fetches a supplied URL."""
    url = public_url(value)
    if (
        not isinstance(value, str)
        or any(ord(c) < 33 for c in value)
        or not url
        or len(url) > 2000
        or "\\" in url
    ):
        return None
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if (
        "." not in host
        or "%" in host
        or host.endswith((".local", ".internal", ".localhost", ".lan"))
    ):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        # Numeric IPv4 shorthand (127.1 / 2130706433) must not become a public link.
        if all(c in "0123456789.xabcdef" for c in host):
            return None
    return url


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def trigrams(value):
    value = "  " + normalize(value) + " "
    return {value[i : i + 3] for i in range(max(0, len(value) - 2))}


def lexical_score(query, title, content):
    q, title, body = normalize(query), normalize(title), normalize(content)
    terms = set(TOKENS.findall(q))
    if not terms:
        return 0.0
    matched = sum(term in title or term in body for term in terms)
    a, b = trigrams(q), trigrams(title + " " + body)
    similarity = len(a & b) / max(1, len(a | b))
    if not matched and similarity < 0.08:
        return 0.0
    return 4.0 * (q in title) + 2.0 * (q in body) + matched / len(terms) + similarity


def nearest_vector_query(query):
    """A bound vector literal; user text never becomes SQL syntax."""
    column = literal_column("market_research_chunks.embedding_vector")
    return (
        query.where(column.is_not(None))
        .order_by(column.op("<=>")(text("CAST(:query_vector AS vector)")), CHUNKS.c.chunk_id)
        .limit(60)
    )


def token_offsets(value, adapter=None):
    offsets = adapter.offsets(value) if adapter else [m.span() for m in TOKENS.finditer(value)]
    last = 0
    for start, end in offsets:
        if not 0 <= start <= end <= len(value) or start < last:
            raise ValueError("Invalid tokenizer offsets")
        last = start
    return [(a, b) for a, b in offsets if a < b]


def chunk_section(section, adapter=None):
    """Exact model-token offsets when supplied; lexical-only chunks have a separate version.

    Tables split within each original row, repeating the exact header and keeping
    header/body offsets separate. No generated context or silently truncated row.
    """
    if not isinstance(section, dict):
        raise ValueError("Invalid section")
    page = section.get("page")
    if page is not None and (type(page) is not int or not 1 <= page <= 100000):
        raise ValueError("Invalid page")
    label = section.get("section", "")
    if not isinstance(label, str) or len(label) > 300:
        raise ValueError("Invalid section label")
    table = section.get("kind") == "table"
    header = section.get("header", "") if table else ""
    rows = section.get("rows", []) if table else [section.get("text", "")]
    if not isinstance(header, str) or not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid section")
    header_tokens = token_offsets(header, adapter)
    if len(header_tokens) > 128:
        raise ValueError("Table header token limit")
    budget = 320 - len(header_tokens)
    for row_index, value in enumerate(rows):
        if not isinstance(value, str) or len(value) > 200000:
            raise ValueError("Section text limit")
        offsets = token_offsets(value, adapter)
        start = 0
        while start < len(offsets):
            end = min(len(offsets), start + budget)
            left, right = offsets[start][0], offsets[end - 1][1]
            body = value[left:right]
            content = header + "\n" + body if header else body
            # Prefix/whitespace token boundaries can differ from concatenated token counts.
            while len(token_offsets(content, adapter)) > 320 and end > start + 1:
                end -= 1
                right = offsets[end - 1][1]
                body = value[left:right]
                content = header + "\n" + body if header else body
            if len(content) > 24000 or len(token_offsets(content, adapter)) > 320:
                raise ValueError("Chunk byte or token limit")
            yield {
                "text": content,
                "section": label,
                "page": page,
                "table": table,
                "row": row_index if table else None,
                "token_start": start,
                "token_end": end,
                "char_start": left,
                "char_end": right,
                "header_char_start": 0 if header else None,
                "header_char_end": len(header) if header else None,
                "token_count": len(token_offsets(content, adapter)),
            }
            if end == len(offsets):
                break
            start = max(start + 1, end - 48)


def safe_document(payload, *, document_id, revision, kind):
    if not isinstance(payload, dict):
        raise ValueError("Invalid evidence document")
    title, url = payload.get("title"), evidence_url(payload.get("url") or payload.get("source_url"))
    if not (
        isinstance(document_id, str)
        and SHA.fullmatch(document_id)
        and isinstance(revision, str)
        and SHA.fullmatch(revision)
        and kind in KINDS
        and isinstance(title, str)
        and 0 < len(title) <= 1000
        and url
    ):
        raise ValueError("Invalid evidence document")
    raw_hash = payload.get("source_hash") or payload.get("source_document_id")
    source_hash = raw_hash if isinstance(raw_hash, str) and SHA.fullmatch(raw_hash) else None
    return {
        "id": document_id,
        "title": title,
        "source_url": url,
        "revision": revision,
        "kind": kind,
        "published_at": payload.get("published_at")
        if day_of(payload.get("published_at"))
        else None,
        "source_hash": source_hash,
        "source_hash_status": "present" if source_hash else "missing",
        "issuer": (payload.get("issuer") or payload.get("publisher") or "")[:300]
        if isinstance(payload.get("issuer") or payload.get("publisher") or "", str)
        else "",
        "review_status": payload.get("review_status")
        if payload.get("review_status") in {"verified", "candidate", "source_observed"}
        else "source_observed",
        "association_basis": payload.get("association_basis")
        if payload.get("association_basis") in {
            "provider_stock_news", "document_issuer", "reviewed_document_subject"
        }
        else None,
        "verified_security_codes": sorted(verified_document_codes(payload)),
    }


@contextmanager
def read_db(store, seconds=1.5):
    """A transaction-local PG timeout; SQLite progress callback is connection-local."""
    with store.read_connection() as db:
        raw = None
        if store.engine.dialect.name == "postgresql":
            db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        elif store.engine.dialect.name == "sqlite":
            raw = db.connection.driver_connection
            end = time.monotonic() + seconds
            raw.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        try:
            yield db
        finally:
            if raw is not None:
                raw.set_progress_handler(None, 0)


def catalogue_state(db, dialect):
    """Read schema capabilities once per request, never a cached schema guess.

    PostgreSQL's old per-table Inspector calls each checked out another connection.
    Keep this metadata query inside the same bounded read transaction as retrieval.
    A migration or newly collected table is therefore visible on the next request.
    """
    if dialect == "postgresql":
        state = (
            db.execute(
                text(
                    "SELECT to_regclass(:heads) IS NOT NULL AS has_heads, "
                    "to_regclass(:chunks) IS NOT NULL AS has_chunks, "
                    "to_regclass(:documents) IS NOT NULL AS has_documents, "
                    "to_regclass(:links) IS NOT NULL AS has_links, "
                    "EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm') AS pg_trgm, "
                    "EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass(:chunks) "
                    "AND attname='embedding_vector' AND NOT attisdropped) AS pg_vectors"
                ),
                {
                    "heads": HEADS.name,
                    "chunks": CHUNKS.name,
                    "documents": DOCUMENTS.name,
                    "links": LINKS.name,
                },
            )
            .mappings()
            .one()
        )
        return (
            bool(state["has_heads"] and state["has_chunks"]),
            bool(state["has_documents"] and state["has_links"]),
            bool(state["pg_trgm"]),
            bool(state["pg_vectors"]),
        )
    inspector = inspect(db)
    return (
        inspector.has_table(HEADS.name) and inspector.has_table(CHUNKS.name),
        inspector.has_table(DOCUMENTS.name) and inspector.has_table(LINKS.name),
        False,
        False,
    )


class ResearchRepository:
    def __init__(
        self,
        store,
        *,
        adapter=None,
        candidate_limit=1000,
        fallback_reason="embedding_not_configured",
    ):
        self.store, self.adapter = store, adapter
        self.fallback_reason = fallback_reason
        if not 1 <= candidate_limit <= 2000:
            raise ValueError("Invalid candidate bound")
        self.candidate_limit = candidate_limit

    def initialize(self):
        """Explicit collector setup only; never called by GET routes."""
        ContextRepository(self.store).initialize()
        META.create_all(self.store.engine)

    def ingest(self, payload, *, codes, recorded_at=None):
        """Append source revisions/chunks and advance one index head atomically."""
        code_map = securities(self.store)
        codes = sorted(set(codes))
        if not codes or any(code not in code_map for code in codes):
            raise ValueError("Evidence requires in-scope securities")
        if len(encoded(payload).encode()) > 2 * 1024**2:
            raise ValueError("Document size limit")
        document_id = payload.get("document_id") or digest(payload.get("url"))
        revision = payload.get("revision") or digest(payload)
        kind = payload.get("kind", "evidence")
        public = safe_document(payload, document_id=document_id, revision=revision, kind=kind)
        sections = payload.get("sections") or []
        if kind == "news" or not sections:
            sections = [{"text": public["title"], "section": "제목"}]
        elif public["source_hash"] is None:
            raise ValueError("Body evidence requires source hash")
        if not isinstance(sections, list) or len(sections) > 1000:
            raise ValueError("Section count limit")
        version = (
            self.adapter.version + ":320:48" + ASSOCIATION_VERSION
            if self.adapter else LEXICAL_VERSION
        )
        indexed = []
        for section in sections:
            for chunk in chunk_section(section, self.adapter):
                if len(indexed) >= 5000:
                    raise ValueError("Document chunk count limit")
                ordinal = len(indexed)
                chunk_id = digest([document_id, revision, version, ordinal])
                metadata = {
                    **public,
                    **{k: v for k, v in chunk.items() if k != "text"},
                    "securities": [{"code": c, "company": code_map[c]["name"]} for c in codes],
                    "index_version": version,
                    "chunk_id": chunk_id,
                }
                vector = None
                if self.adapter:
                    try:
                        vector = self.adapter.encode(chunk["text"])
                    except EmbeddingUnavailable:
                        pass  # Lexical chunk remains usable; never fill a fake vector.
                indexed.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "revision": revision,
                        "index_version": version,
                        "ordinal": ordinal,
                        "search_text": chunk["text"],
                        "metadata": encoded(metadata),
                        "embedding": encoded(vector) if vector else None,
                        "embedding_version": self.adapter.version if vector else None,
                    }
                )
        stamp = recorded_at or now()
        if day_of(stamp) is None:
            raise ValueError("Invalid source receipt")
        with self.store.engine.begin() as db:
            old_chunks = (
                db.execute(
                    select(CHUNKS).where(
                        CHUNKS.c.document_id == document_id,
                        CHUNKS.c.revision == revision,
                        CHUNKS.c.index_version == version,
                    )
                )
                .mappings()
                .all()
            )
            if old_chunks:
                original = {r["chunk_id"]: (r["search_text"], r["metadata"]) for r in old_chunks}
                proposed = {r["chunk_id"]: (r["search_text"], r["metadata"]) for r in indexed}
                if original != proposed:
                    raise ValueError("Immutable document revision conflict")
            # Old chunk generations remain addressable; no source or revision deletion.
            insert_ignore(
                self.store,
                db,
                DOCUMENTS,
                [
                    {
                        "document_id": document_id,
                        "revision": revision,
                        "kind": kind,
                        "published_day": day_of(public["published_at"]),
                        "published_at": public["published_at"],
                        "recorded_at": stamp,
                        "payload": encoded({**public, "url": public["source_url"]}),
                    }
                ],
            )
            insert_ignore(self.store, db, CHUNKS, indexed)
            # A concurrent first writer may have won INSERT ... DO NOTHING after the
            # precheck. Validate the actual immutable generation before advancing head.
            persisted = (
                db.execute(
                    select(CHUNKS).where(
                        CHUNKS.c.document_id == document_id,
                        CHUNKS.c.revision == revision,
                        CHUNKS.c.index_version == version,
                    )
                )
                .mappings()
                .all()
            )
            if {r["chunk_id"]: (r["search_text"], r["metadata"]) for r in persisted} != {
                r["chunk_id"]: (r["search_text"], r["metadata"]) for r in indexed
            }:
                raise ValueError("Immutable document revision conflict")
            insert_ignore(
                self.store, db, LINKS, [dict(document_id=document_id, code=c) for c in codes]
            )
            if self.store.engine.dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert
            else:
                from sqlalchemy.dialects.postgresql import insert
            statement = insert(HEADS).values(
                document_id=document_id,
                revision=revision,
                index_version=version,
                kind=kind,
                published_day=day_of(public["published_at"]),
                recorded_at=stamp,
            )
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=[HEADS.c.document_id],
                    set_={
                        c.name: getattr(statement.excluded, c.name)
                        for c in HEADS.c
                        if c.name != "document_id"
                    },
                    where=or_(
                        HEADS.c.recorded_at < stamp,
                        and_(HEADS.c.recorded_at == stamp, HEADS.c.revision <= revision),
                    ),
                )
            )
        return len(indexed)

    def project_context(self, *, after=None, limit=100):
        """Bounded keyset import of existing local documents; caller saves next cursor."""
        if not 1 <= limit <= 500:
            raise ValueError("Projection batch limit")
        query = (
            select(DOCUMENTS)
            .order_by(DOCUMENTS.c.recorded_at, DOCUMENTS.c.document_id, DOCUMENTS.c.revision)
            .limit(limit + 1)
        )
        if after:
            stamp, identity, revision = after
            query = query.where(
                or_(
                    DOCUMENTS.c.recorded_at > stamp,
                    and_(DOCUMENTS.c.recorded_at == stamp, DOCUMENTS.c.document_id > identity),
                    and_(
                        DOCUMENTS.c.recorded_at == stamp,
                        DOCUMENTS.c.document_id == identity,
                        DOCUMENTS.c.revision > revision,
                    ),
                )
            )
        with read_db(self.store) as db:
            records = db.execute(query).mappings().all()
            links = (
                db.execute(
                    select(LINKS).where(
                        LINKS.c.document_id.in_([r["document_id"] for r in records[:limit]])
                    )
                )
                .mappings()
                .all()
                if records
                else []
            )
        groups = {}
        for link in links:
            groups.setdefault(link["document_id"], []).append(link["code"])
        rejected = 0
        for row in records[:limit]:
            try:
                self.ingest(
                    {
                        **json.loads(row["payload"]),
                        "document_id": row["document_id"],
                        "revision": row["revision"],
                        "kind": row["kind"],
                    },
                    codes=groups.get(row["document_id"], []),
                    recorded_at=row["recorded_at"],
                )
            except (ValueError, TypeError, KeyError):
                rejected += 1
        last = records[min(len(records), limit) - 1] if records else None
        return {
            "processed": min(len(records), limit),
            "rejected": rejected,
            "has_more": len(records) > limit,
            "next": [last[k] for k in ("recorded_at", "document_id", "revision")]
            if last
            else after,
        }

    def _candidates(
        self,
        *,
        code=None,
        kind=None,
        from_day=None,
        to_day=None,
        document_id=None,
        revision=None,
        query=None,
        query_vector=None,
    ):
        with read_db(self.store) as db:
            indexed, context, pg_trgm, pg_vectors = catalogue_state(
                db, self.store.engine.dialect.name
            )
            if not context:
                return [], 0, False, "not_collected"
            ranked = select(
                DOCUMENTS,
                func.row_number()
                .over(
                    partition_by=DOCUMENTS.c.document_id,
                    order_by=(DOCUMENTS.c.recorded_at.desc(), DOCUMENTS.c.revision.desc()),
                )
                .label("n"),
            ).subquery()
            q = select(ranked).where(ranked.c.n == 1)
            if document_id:
                q = q.where(ranked.c.document_id == document_id)
            if revision:
                # Explicit versions do not silently fall back to another revision.
                q = select(DOCUMENTS).where(
                    DOCUMENTS.c.document_id == document_id, DOCUMENTS.c.revision == revision
                )
            if code:
                q = q.where(
                    q.selected_columns.document_id.in_(
                        select(LINKS.c.document_id).where(LINKS.c.code == code)
                    )
                )
                q = q.where(verified_document_filter(
                    q.selected_columns.payload, code, self.store.engine.dialect.name
                ))
            if kind:
                q = q.where(q.selected_columns.kind == kind)
            if from_day:
                q = q.where(q.selected_columns.published_day >= from_day)
            if to_day:
                q = q.where(q.selected_columns.published_day <= to_day)
            available = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
            q = q.where(func.length(q.selected_columns.payload) <= 64000)
            scope = q.subquery()
            title = func.lower(
                func.json_extract(scope.c.payload, "$.title")
                if self.store.engine.dialect.name == "sqlite"
                else scope.c.payload.cast(JSON)["title"].as_string()
            )
            terms = list(dict.fromkeys(TOKENS.findall(normalize(query or ""))))[:12]
            title_match = (
                or_(*(title.contains(t, autoescape=True) for t in terms)) if terms else None
            )
            if query and pg_trgm:
                title_match = or_(title_match, title.op("%>")(normalize(query)))
            elif query:
                fragments = sorted(t for t in trigrams(query) if len(t.strip()) == 3)[:12]
                if fragments:
                    title_match = or_(
                        title_match, *(title.contains(t, autoescape=True) for t in fragments)
                    )
            head_match = and_(
                CHUNKS.c.document_id == HEADS.c.document_id,
                CHUNKS.c.revision == HEADS.c.revision,
                CHUNKS.c.index_version == HEADS.c.index_version,
            )
            chunk_base = select(CHUNKS).join(
                scope,
                and_(
                    CHUNKS.c.document_id == scope.c.document_id,
                    CHUNKS.c.revision == scope.c.revision,
                ),
            )
            if not revision:
                chunk_base = chunk_base.join(HEADS, head_match)
            else:
                # An explicit historical revision keeps its own chunk generation.
                versions = (
                    db.execute(
                        select(CHUNKS.c.index_version)
                        .where(CHUNKS.c.document_id == document_id, CHUNKS.c.revision == revision)
                        .distinct()
                        .order_by(CHUNKS.c.index_version)
                        .limit(16)
                    )
                    .scalars()
                    .all()
                    if indexed
                    else []
                )
                preferred = (
                    self.adapter.version + ":320:48" + ASSOCIATION_VERSION
                    if self.adapter else LEXICAL_VERSION
                )
                chosen = preferred if preferred in versions else versions[0] if versions else ""
                chunk_base = chunk_base.where(CHUNKS.c.index_version == chosen)
            chunk_base = chunk_base.where(
                func.length(CHUNKS.c.search_text) <= 24000,
                func.length(CHUNKS.c.metadata) <= 16000,
                or_(CHUNKS.c.embedding.is_(None), func.length(CHUNKS.c.embedding) <= 20000),
            )
            body = func.lower(CHUNKS.c.search_text)
            body_match = or_(*(body.contains(t, autoescape=True) for t in terms)) if terms else None
            if query and pg_trgm:
                body_match = or_(body_match, body.op("%>")(normalize(query)))
            elif query:
                if fragments:
                    body_match = or_(
                        body_match, *(body.contains(t, autoescape=True) for t in fragments)
                    )
            vector_chunks = []
            vector_query = (
                chunk_base.where(CHUNKS.c.embedding_version == self.adapter.version)
                if query_vector is not None
                else None
            )
            if vector_query is not None and indexed:
                if pg_vectors:
                    vector_chunks = (
                        db.execute(
                            nearest_vector_query(vector_query),
                            {"query_vector": encoded(query_vector)},
                        )
                        .mappings()
                        .all()
                    )
                elif self.store.engine.dialect.name == "sqlite":
                    # Portable fixture/development fallback, explicitly bounded rather than ANN.
                    vector_chunks = (
                        db.execute(
                            vector_query.order_by(CHUNKS.c.chunk_id).limit(self.candidate_limit + 1)
                        )
                        .mappings()
                        .all()
                    )
            vector_ids = {r["document_id"] for r in vector_chunks}
            filtered = select(scope)
            if query:
                document_match = title_match
                if indexed:
                    matched = (
                        chunk_base.where(body_match)
                        .with_only_columns(CHUNKS.c.document_id)
                        .subquery()
                    )
                    document_match = or_(
                        document_match, scope.c.document_id.in_(select(matched.c.document_id))
                    )
                if vector_ids:
                    document_match = or_(document_match, scope.c.document_id.in_(vector_ids))
                filtered = filtered.where(document_match)
            # SQL keyword/company/date filtering happens before the bound, so old matching
            # evidence is not silently discarded merely because newer documents exist.
            records = (
                db.execute(
                    filtered.order_by(scope.c.published_at.desc(), scope.c.document_id).limit(
                        self.candidate_limit + 1
                    )
                )
                .mappings()
                .all()
            )
            record_overflow = len(records) > self.candidate_limit
            records = records[: self.candidate_limit]
            identities = [r["document_id"] for r in records]
            associations = (
                db.execute(
                    select(LINKS)
                    .where(LINKS.c.document_id.in_(identities))
                    .limit(self.candidate_limit * 20)
                )
                .mappings()
                .all()
                if identities
                else []
            )
            links = {}
            for association in associations:
                links.setdefault(association["document_id"], []).append(association["code"])
            chunks = []
            if indexed and identities:
                chunk_query = chunk_base.where(CHUNKS.c.document_id.in_(identities))
                if query:
                    chunk_query = chunk_query.where(or_(body_match, title_match))
                chunk_query = chunk_query.order_by(CHUNKS.c.document_id, CHUNKS.c.ordinal).limit(
                    self.candidate_limit + 1
                )
                chunks = db.execute(chunk_query).mappings().all()
                seen = {r["chunk_id"] for r in chunks}
                chunks.extend(
                    r
                    for r in vector_chunks[: self.candidate_limit]
                    if r["chunk_id"] not in seen and r["document_id"] in identities
                )
        result, covered = [], set()
        current = {(r["document_id"], r["revision"]) for r in records}
        documents = {(r["document_id"], r["revision"]): r for r in records}
        for row in chunks[: self.candidate_limit]:
            if (row["document_id"], row["revision"]) not in current:
                continue  # Stale projection never hides a newer title/correction.
            try:
                metadata = json.loads(row["metadata"])
                checked = safe_document(
                    json.loads(documents[(row["document_id"], row["revision"])]["payload"]),
                    document_id=row["document_id"],
                    revision=row["revision"],
                    kind=documents[(row["document_id"], row["revision"])]["kind"],
                )
                names = {
                    r.get("code"): r.get("company")
                    for r in metadata.get("securities", []) if isinstance(r, dict)
                } if isinstance(metadata.get("securities"), list) else {}
                result.append(
                    {
                        **metadata,
                        **checked,
                        "securities": [
                            {"code": c, "company": names.get(c)}
                            for c in links.get(row["document_id"], [])
                        ],
                        "excerpt": checked["title"]
                        if checked["kind"] == "news"
                        else row["search_text"],
                        "_vector": row["embedding"],
                        "_vector_version": row["embedding_version"],
                    }
                )
                covered.add(row["document_id"])
            except (ValueError, TypeError):
                continue
        for row in records:
            if row["document_id"] in covered:
                continue
            try:
                public = safe_document(
                    json.loads(row["payload"]),
                    document_id=row["document_id"],
                    revision=row["revision"],
                    kind=row["kind"],
                )
                result.append(
                    {
                        **public,
                        "chunk_id": digest([public["id"], public["revision"], "title"]),
                        "excerpt": public["title"],
                        "section": "제목",
                        "page": None,
                        "securities": [
                            {"code": c, "company": None} for c in links.get(public["id"], [])
                        ],
                        "index_version": "context_titles_only",
                    }
                )
            except (ValueError, TypeError):
                continue
        truncated = record_overflow or len(chunks) > self.candidate_limit
        return (
            result,
            available,
            truncated,
            "indexed_and_titles" if chunks else "context_titles_only",
        )

    def search(self, q, *, code=None, kind=None, from_day=None, to_day=None, limit=10):
        validate_filters(q, code, kind, from_day, to_day, limit)
        started = time.monotonic()
        query_vector, vector_error = None, None
        if self.adapter:
            try:
                query_vector = normalized_vector(self.adapter.encode(q, query=True))
            except (EmbeddingUnavailable, ValueError, TypeError):
                vector_error = "embedding_unavailable"
        rows, available, truncated, index = self._candidates(
            code=code,
            kind=kind,
            from_day=from_day,
            to_day=to_day,
            query=q,
            query_vector=query_vector,
        )
        ranked = []
        ranking_deadline = time.monotonic() + 0.75
        for row in rows:
            if time.monotonic() >= ranking_deadline:
                truncated = True
                break
            ranked.append((lexical_score(q, row["title"], row["excerpt"]), row))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"], pair[1]["chunk_id"]))
        lexical = [r for score, r in ranked if score > 0][:60]
        mode, reason = "lexical_only", self.fallback_reason
        vectors = []
        if self.adapter:
            reason = vector_error or "embedding_not_indexed"
            try:
                if query_vector is None:
                    raise EmbeddingUnavailable("embedding_unavailable")
                for row in rows:
                    if row.get("_vector_version") != self.adapter.version or not row.get("_vector"):
                        continue
                    vector = normalized_vector(json.loads(row["_vector"]))
                    vectors.append(
                        (sum(a * b for a, b in zip(query_vector, vector, strict=True)), row)
                    )
                vectors.sort(key=lambda pair: (-pair[0], pair[1]["id"], pair[1]["chunk_id"]))
                if vectors:
                    mode, reason = "hybrid_rrf", None
            except (EmbeddingUnavailable, ValueError, TypeError):
                reason = "embedding_unavailable"
        scores, selected = {}, {}
        for ranking in (lexical, [row for _, row in vectors[:60]]):
            for rank, row in enumerate(ranking, 1):
                key = row["chunk_id"]
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                selected[key] = row
        ordered = sorted(selected, key=lambda key: (-scores[key], selected[key]["id"], key))
        items = [public_item(selected[key], code) for key in ordered[:limit]]
        return {
            "status": "ready" if items else "no_matches" if available else "not_collected",
            "mode": mode,
            "fallback_reason": reason,
            "available_documents": available,
            "items": items,
            "coverage": {
                "index": index,
                "candidate_limit": self.candidate_limit,
                "truncated": truncated,
                "ranking_scope": "bounded_collected_documents",
                "candidate_filter": "sql_keyword_trigram_or_vector_before_limit",
                "lexical_candidates_scored": len(ranked),
            },
            "revision": digest([[r["chunk_id"], r["revision"]] for r in items]),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "version": VERSION,
        }

    def document(self, document_id, revision=None):
        if not SHA.fullmatch(document_id) or (revision and not SHA.fullmatch(revision)):
            raise ValueError("Invalid document identity")
        rows, _, truncated, index = self._candidates(document_id=document_id, revision=revision)
        if not rows:
            return None
        first = public_item(rows[0])
        chunks, size = [], 0
        for row in rows[:100]:
            item = public_item(row)
            size += len(encoded(item).encode())
            if size > 1024**2:
                truncated = True
                break
            chunks.append(item)
        return {
            **{k: v for k, v in first.items() if k not in ("excerpt", "chunk_id")},
            "chunks": chunks,
            "coverage": {"index": index, "truncated": truncated or len(rows) > 100},
            "version": VERSION,
        }


def public_item(row, code=None):
    # Allowlist; never leak raw bodies, local paths, arbitrary source metadata or vectors.
    keys = (
        "id",
        "chunk_id",
        "title",
        "excerpt",
        "source_url",
        "published_at",
        "revision",
        "source_hash",
        "source_hash_status",
        "kind",
        "issuer",
        "review_status",
        "association_basis",
        "section",
        "page",
        "table",
        "row",
        "token_start",
        "token_end",
        "char_start",
        "char_end",
        "header_char_start",
        "header_char_end",
        "token_count",
        "index_version",
    )
    result = {key: row.get(key) for key in keys}
    raw_associations = row.get("securities", [])
    if not isinstance(raw_associations, list):
        raw_associations = []
    associations = [
        {
            "code": r["code"],
            "company": r.get("company") if isinstance(r.get("company"), str) else None,
        }
        for r in raw_associations[:20]
        if isinstance(r, dict)
        and isinstance(r.get("code"), str)
        and re.fullmatch(r"[A-Z0-9]{6}", r["code"])
    ]
    verified = verified_document_codes(row)
    candidates = [
        {**r, "review_status": "candidate", "association_basis": row.get("association_basis")}
        for r in associations if r["code"] not in verified
    ]
    associations = [r for r in associations if r["code"] in verified]
    for key in (
        "page",
        "row",
        "token_start",
        "token_end",
        "char_start",
        "char_end",
        "header_char_start",
        "header_char_end",
        "token_count",
    ):
        if result[key] is not None and (type(result[key]) is not int or result[key] < 0):
            result[key] = None
    for key, maximum in (("section", 300), ("index_version", 200)):
        if not isinstance(result[key], str) or len(result[key]) > maximum:
            result[key] = None
    result["table"] = result["table"] is True
    selected = next(
        (r for r in associations if r.get("code") == code), associations[0] if associations else {}
    )
    result.update(
        code=selected.get("code"), company=selected.get("company"), securities=associations,
        candidate_securities=candidates,
        association_status="verified" if associations else "unverified",
    )
    if row.get("kind") == "news":
        result["excerpt"] = row["title"]
    return result


def validate_filters(q, code=None, kind=None, from_day=None, to_day=None, limit=10):
    if not isinstance(q, str) or not 1 <= len(q.strip()) <= 200 or any(ord(c) < 32 for c in q):
        raise ValueError("검색어는 1~200자로 입력해 주세요.")
    if code and not re.fullmatch(r"[A-Z0-9]{6}", code):
        raise ValueError("종목코드를 확인해 주세요.")
    if kind and kind not in KINDS:
        raise ValueError("자료 종류를 확인해 주세요.")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("결과 개수를 확인해 주세요.")
    for value in (from_day, to_day):
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError("날짜를 확인해 주세요.")
    if from_day and to_day and from_day > to_day:
        raise ValueError("시작일과 종료일을 확인해 주세요.")


class SearchBudget:
    """Process-wide bounded rate and concurrency; no spoofable forwarded-IP keys."""

    def __init__(self, *, per_minute=120, concurrent=4, queued=20, wait_seconds=0.75):
        self.rate, self.active, self.events = (
            per_minute,
            threading.BoundedSemaphore(concurrent),
            deque(),
        )
        self.lock = threading.Lock()
        self.admitted = threading.BoundedSemaphore(concurrent + queued)
        self.reserved = 0
        self.wait_seconds = wait_seconds

    @contextmanager
    def acquire(self):
        if not self.admitted.acquire(blocking=False):
            raise EmbeddingUnavailable("search_busy")
        acquired = False
        try:
            with self.lock:
                current = time.monotonic()
                while self.events and self.events[0] <= current - 60:
                    self.events.popleft()
                if len(self.events) + self.reserved >= self.rate:
                    raise EmbeddingUnavailable("search_busy")
                self.reserved += 1
            # Waiting outside the rate lock lets current readers finish. Queue
            # size and deadline remain bounded; waiting is not an execution slot.
            acquired = self.active.acquire(timeout=self.wait_seconds)
            with self.lock:
                self.reserved -= 1
                if acquired:
                    self.events.append(time.monotonic())
            if not acquired:
                raise EmbeddingUnavailable("search_busy")
            yield
        finally:
            if acquired:
                self.active.release()
            self.admitted.release()


def attach_routes(app, base, get_store, *, adapter=None):
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    budget = SearchBudget()
    fallback_reason = "embedding_not_configured"
    if adapter is None:
        # Route attachment is app construction, never a public request/lazy download.
        adapter, fallback_reason = load_from_env()

    def response(work):
        try:
            with budget.acquire():
                result = work()
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except EmbeddingUnavailable as exc:
            raise HTTPException(
                429, "잠시 후 다시 검색해 주세요.", headers={"Retry-After": "1"}
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, "검색 조건을 확인해 주세요.") from exc
        except SQLAlchemyError as exc:
            raise HTTPException(503, "자료를 불러오지 못했어요.") from exc

    @app.get(base + "/research/search")
    def research_search(
        q: str = Query(..., min_length=1, max_length=200),
        code: str | None = None,
        kind: str | None = None,
        from_day: str | None = Query(None, alias="from"),
        to_day: str | None = Query(None, alias="to"),
        limit: int = Query(10, ge=1, le=20),
    ):
        return response(
            lambda: ResearchRepository(
                get_store(), adapter=adapter, fallback_reason=fallback_reason
            ).search(q, code=code, kind=kind, from_day=from_day, to_day=to_day, limit=limit)
        )

    @app.get(base + "/research/documents/{document_id}")
    def research_document(document_id: str, revision: str | None = None):
        def work():
            result = ResearchRepository(get_store(), adapter=adapter).document(
                document_id, revision
            )
            if result is None:
                raise HTTPException(404, "문서를 찾을 수 없어요.")
            return result

        return response(work)
