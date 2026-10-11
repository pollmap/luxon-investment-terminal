-- OPTIONAL OPERATOR MIGRATION. Never run from a public request or app import.
-- Prerequisite: ResearchRepository.initialize() created the two empty projection tables.
-- Install compatible pgvector server binaries first; this SQL does not download anything.
-- Review disk/backup/rollback before running. Current disk-pressure rollout stays OFF.
-- Existing source documents, revisions, news metadata and collector tables are untouched.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE market_research_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector(384);

-- New projection writes have a validated normalized 384-vector JSON or NULL.
-- Backfill is deliberately not automatic. An explicit bounded batch may UPDATE
-- embedding=embedding WHERE chunk_id IN (<reviewed batch>) after space recovery.
CREATE OR REPLACE FUNCTION market_research_vector_projection() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.embedding IS NULL THEN
        NEW.embedding_vector := NULL;
    ELSE
        NEW.embedding_vector := NEW.embedding::vector(384);
        IF vector_norm(NEW.embedding_vector) < 0.999
            OR vector_norm(NEW.embedding_vector) > 1.001 THEN
            RAISE EXCEPTION 'research_embedding_not_normalized';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS market_research_vector_projection ON market_research_chunks;
CREATE TRIGGER market_research_vector_projection
BEFORE INSERT OR UPDATE OF embedding ON market_research_chunks
FOR EACH ROW EXECUTE FUNCTION market_research_vector_projection();
COMMIT;

-- Separate concurrent builds preserve public readers. A failed build may leave an
-- invalid index: inspect pg_index.indisvalid before retrying; do not report success.
SET lock_timeout = '3s';
SET statement_timeout = '5min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS market_research_title_trgm_idx
    ON market_context_documents USING gin (lower((payload::json)->>'title') gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS market_research_text_trgm_idx
    ON market_research_chunks USING gin (lower(search_text) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS market_research_vector_hnsw_idx
    ON market_research_chunks USING hnsw (embedding_vector vector_cosine_ops)
    WITH (m = 8, ef_construction = 32);
RESET lock_timeout;
RESET statement_timeout;

-- Runtime detects installed extension/column with read-only metadata queries.
-- Keyword predicates and code/kind/date filters apply before candidate LIMIT.
-- Vector SQL orders embedding_vector <=> :query_vector then RRF combines ranks.
-- HNSW filtering can reduce recall; results are bounded candidates, never all-corpus proof.
