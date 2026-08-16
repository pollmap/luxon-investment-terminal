# LUXON Migration Ledger

Status: public snapshot authority
As of: 2026-08-16
Canonical repository: `pollmap/luxon-investment-terminal`

## Public-history decision

The canonical public repository starts from a sanitized single-root snapshot.
The complete pre-public history is retained in a separate owner-controlled
private archive. Private repository names, local remote aliases, historical
commit identifiers, personal filesystem paths, and private design identifiers
are intentionally not part of this ledger.

This boundary prevents public history from exposing author email metadata,
local references, superseded authenticated-capture tooling, or private design
records while preserving recovery and provenance for the owner.

## Migration rules

- A candidate repository is not evidence that code or data has been imported.
- Every future transplant records an owner-approved source, immutable revision,
  source paths, target paths, authorship/license boundary, and verification
  result in a private workpaper before merge.
- Only the resulting reviewed code and a non-sensitive decision summary enter
  this public ledger.
- Secrets, local databases, API responses, raw filings, warehouse outputs,
  private portfolio data, competition datasets, commercial screenshots, and
  protected DOM/content never migrate through public Git.
- Fixtures remain `fixture_non_production` and cannot become research or
  production evidence.
- External projects may contribute independently reviewed patterns only. Their
  product scope, brand, data, generated artifacts, and full history are not
  merged wholesale.
- LUXON keeps its own brand, UI assets, formulas, contracts, and source-trace
  rules. External products are workflow references, not product assets.

## Accepted public baseline

| Date | Scope | Ownership and license check | Verification | Result |
| --- | --- | --- | --- | --- |
| 2026-08-16 | Sanitized LUXON application, tests, public docs, local operations, and secret-free CI | Owner-controlled source; third-party assets and protected captures excluded; no open-source license granted | Secret scan, source scan, test/lint/type/build/browser gates, and clean-root commit metadata | accepted public baseline |

## Data migration boundary

The beta starts with empty operator-controlled Postgres and local raw/warehouse
mounts. Data is not copied merely because an earlier private project contains
it. A future import requires source, retrieval time, license, checksum, schema
version, and `source_trace.available_at`, then passes the normal validation and
quality gates.
