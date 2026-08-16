# Quality Flags

- `gaap_fallback`: adjusted EPS unavailable, GAAP diluted EPS used.
- `inferred_tax_effect`: tax effect not disclosed and estimated from effective tax rate.
- `explicit_tax_effect`: tax effect disclosed directly in source table.
- `net_of_tax_amount`: source presents net-of-tax adjustment.
- `no_tax_benefit_assumed`: generally used for goodwill impairment.
- `missing_pretax_income`: effective tax rate fallback used.
- `missing_tax_expense`: effective tax rate fallback used.
- `abnormal_effective_tax_rate`: rate clamped to reasonableness bounds.
- `inferred_shares`: diluted shares inferred from NI/EPS.
- `eps_bridge_outside_tolerance`: EPS bridge does not reconcile within tolerance.
- `asymmetric_adjustment`: gain/loss treatment may be one-sided.
- `recurring_adjustment`: adjustment category repeats across periods.

