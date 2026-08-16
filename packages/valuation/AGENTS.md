# Valuation Agent Rules

- Valuation functions must be deterministic and pure where practical.
- Derived values require explicit formula names and input fact ids.
- Forecasting separates consensus snapshots, user inputs, deterministic
  formulas, and AI commentary. AI does not generate numbers.
- Add or update golden/unit tests for EPS, growth, normal multiple, fair value,
  total return, dividend, and margin-of-safety changes.
