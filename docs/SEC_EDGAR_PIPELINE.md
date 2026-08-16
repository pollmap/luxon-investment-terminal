# SEC EDGAR Pipeline

## Collection

1. Map ticker to CIK from SEC `company_tickers.json`.
2. Fetch `data.sec.gov/submissions/CIK##########.json`.
3. Filter 8-K filings with Item 2.02.
4. Find `EX-99`, `EX-99.1`, or earnings release documents.
5. Cache all responses in `storage/cache/sec`.
6. Persist raw documents in `storage/raw/sec`.
7. Queue raw documents for Vercel Blob sync in `storage/blob_queue`.
8. Normalize adjusted earnings with S1 first, then S2/S3/S4 fallback through the strategy waterfall.

Live collection requires:

```powershell
$env:SEC_USER_AGENT="PersonalFastGraphs/0.1 your-email@example.com"
```

Production-oriented run:

```powershell
$env:DATA_BACKEND="postgres"
$env:DATABASE_URL="postgresql://..."
python -m services.ingestion_worker.cli collect-sec-bulk --archives companyfacts,submissions --persist
python -m services.ingestion_worker.cli load-sec-bulk-warehouse --tickers AAPL,NVDA,CRM,O,JPM --persist
python -m services.ingestion_worker.cli normalize-us --ticker AAPL --years 2020:2025 --persist
python -m services.ingestion_worker.cli secret-audit --strict
pnpm blob:sync:dry-run
pnpm blob:sync
```

`collect-sec-bulk` stores SEC official `companyfacts.zip` and `submissions.zip`
as content-hashed raw archives. `load-sec-bulk-warehouse` reads those local ZIP
archives, extracts selected valuation-relevant us-gaap facts, writes the
source-level rows to `financial_facts`, and promotes FY representative facts
into `metric_values`.

`pnpm blob:sync:dry-run` checks queued Blob manifests and local raw-file paths
without requiring `BLOB_READ_WRITE_TOKEN`. Use it before the actual upload so
missing local payloads or stale result manifests are caught before Vercel Blob
sync starts.

`python -m services.ingestion_worker.cli secret-audit --strict` should pass
before Blob sync. It scans source metadata and queue/cache JSON for unredacted
credentials, while redacting any finding evidence in the audit output.

`backend.normalize.cli normalize` defaults to live SEC collection. Use `--fixture` only for non-production fixture inspection.

## Parse Failures

Failures should be written under:

```txt
storage/parse_failures/{ticker}/{accession_number}/
```

with candidate tables, warnings, and failure reasons.
