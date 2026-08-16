# LUXON Investment Terminal P0

## Objective

Build the P0 foundation for a product-grade fundamentals valuation terminal:

1. Repo structure is explicit.
2. `source_trace` is a storage-gating data contract.
3. AAPL has a non-production E2E contract stub for raw filing, normalized fact,
   and derived metric flow.
4. The implementation keeps FAST Graph-style analytical grammar while using
   original UI, code, calculations, and brand assets.

## Current Repo Tree

The active repository already uses a Vercel-first structure. It is close to the
target PRD, but the existing FastAPI app lives under `services/api` rather than
`apps/api`.

```txt
apps/
  web/                    Next.js + TypeScript terminal UI
backend/
  normalize/              S1/S2/S3/S4 adjusted earnings engine
services/
  api/                    FastAPI read API and Vercel Python function entry
  ingestion_worker/       CLI and scheduled ingestion workflows
packages/
  connectors/             SEC, OpenDART, EDINET, J-Quants, FRED, pykrx paths
  core/                   Canonical source_trace and financial data contracts
  quality/                Validation and source coverage rules
  valuation/              Valuation map, forecast, return, and portfolio math
db/                       Alembic and SQL assets
data/                     raw and warehouse development paths
storage/                  cache, parse failures, chart renders, Blob queue
docs/                     formula book, data dictionary, design and ops docs
tests/                    pytest and Playwright tests
```

## P0 Structure Decision

Do not move `services/api` to `apps/api` during P0. The current Vercel routing,
tests, docs, and deployment scripts already expect `services/api`. The PRD
target can still be satisfied by treating `services/api` as the FastAPI app
package and documenting this compatibility decision. A later migration can add
`apps/api` as a thin wrapper if needed.

## Source Trace Contract

Canonical model:

```txt
packages/core/source_trace.py::SourceTrace
```

Storage-ready values must include:

```txt
source
filing_id
period
unit
currency
method
formula
```

Derived values must additionally carry:

```txt
input_fact_ids
formula
quality_flags
confidence
version
```

The contract accepts legacy aliases such as `source_type`, `accession_number`,
and `form_type`, but durable writes should call:

```py
source_trace.assert_storage_ready()
```

## AAPL E2E Stub

The AAPL P0 stub is intentionally non-production. It exists to lock the pipeline
shape, not to provide real investment data.

```txt
build_aapl_e2e_stub()
  -> EntityIdentifier
  -> RawFilingManifest
  -> NormalizedFact(gaap_eps_diluted, dividend_per_share)
  -> DerivedMetric(adjusted_operating_eps)
```

The stub keeps `source=TEST_FIXTURE` and `quality_flags=["fixture_non_production"]`.
It must never be promoted as research or production evidence.

## Immediate Next Steps

1. Wire storage paths so persisted normalized facts reject missing
   `source_trace`.
2. Add a real SEC AAPL collector run that stores raw filings append-only before
   any normalization.
3. Promote SEC facts into `normalized_facts` and derive
   `adjusted_operating_eps` with formula and input fact ids.
4. Add golden tests for valuation map lines once source-backed AAPL facts are
   present.
