# Adjusted Earnings Engine

## Objective

Reconstruct Adjusted / Operating EPS from first-party source documents with traceable, deterministic logic.

## Strategy Waterfall

1. **S1 SEC reconciliation**: parse 8-K Item 2.02 / Ex.99.1 GAAP to non-GAAP reconciliation tables.
2. **S2 XBRL special items**: compute programmatic add-backs from mapped XBRL facts.
3. **S3 market standard**: future KR/JP mappings for standardized operating or recurring income lines.
4. **S4 GAAP fallback**: use GAAP diluted EPS and label it clearly.

## Policy Toggles

- `street_comparable`: mirrors company-excluded items when S1 is available.
- `core`: includes only clearly non-recurring or non-operating items.
- `exclude_sbc`: user toggle for stock-based compensation add-back.
- `exclude_acquired_intangible_amortization`: user toggle for acquired intangibles amortization.
- `sector_policy`: default, REIT, bank, or insurance.

## Quality Flags

- `inferred_tax_effect`
- `no_tax_benefit_assumed`
- `missing_pretax_income`
- `inferred_shares`
- `eps_reconciliation_outside_tolerance`
- `recurring_adjustment`
- `asymmetric_adjustment`
- `gaap_fallback`

## Limitation

Fixtures in this MVP are synthetic non-production shapes for parser validation. Production use requires live SEC source capture and real golden files.

