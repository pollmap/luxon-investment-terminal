# LUXON Product Positioning

## Product Definition

LUXON Investment Terminal is a source-audited equity research product, not a
single-user personal utility.

The first release can be protected, invite-only, and operator-run while the data
contracts mature. That deployment model is an access-control choice, not the
product boundary.

## Target User

LUXON is built for investors, student investment clubs, research operators, and
small teams that need to inspect valuation claims from source-backed financial
data.

The core user problem:

- stock platforms show ratios quickly,
- filings and XBRL contain the evidence,
- forecasts require assumptions,
- but most tools do not connect the displayed number to the source document,
  formula, method, confidence, and quality flags.

LUXON solves that gap.

## Product Promise

Every number should be explainable.

For any displayed EPS, valuation line, forecast return, portfolio result, or
screening output, the product should answer:

- where the value came from,
- when it became knowable,
- how it was transformed,
- whether it is source-backed, user-entered, fixture-only, or deterministic,
- and what warnings apply.

## Productization Requirements

The product path requires more than a working chart:

- multi-user auth and tenant-safe owner scoping,
- source-backed production mode with fixture fallback blocked,
- data-source license and terms review per connector,
- per-user watchlists, portfolios, saved chart layouts, and audit exports,
- rate limits, logging, and deployment smoke checks,
- clear non-advice positioning,
- pricing/billing readiness later, without coupling billing to the valuation
  engine.

## Current Stage

Current stage: protected early-access product build.

This means:

- Vercel-first deployment remains correct.
- Private repository and allowlisted auth remain correct for early access.
- The UI and docs should not describe the terminal as merely personal.
- Engineering must continue to preserve product-grade provenance, auditability,
  and source coverage.

## Non-Negotiables

- LLMs never generate financial numbers.
- Fixture data is never production evidence.
- Source traces are product surface, not backend-only metadata.
- FAST Graph-style analytical grammar can inspire workflow, but LUXON uses its
  own UI, brand, code, formulas, and data contracts.
