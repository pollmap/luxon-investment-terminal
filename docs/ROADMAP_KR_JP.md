# Roadmap: KR and JP

## Korea

- OpenDART XBRL and financial statement APIs.
- Default S3 metric: operating profit and net income attributable to controlling shareholders.
- Adjusted EPS is not treated as a universal Korean standard.

## Japan

- EDINET / TDnet / J-Quants connectors.
- `collect-edinet` preserves annual securities report metadata and XBRL/CSV
  ZIP source evidence before JP statement normalization.
- `collect-jquants` is the first JP data-lake runner for seed-universe daily
  quotes, statements, and dividends.
- Default S3 metrics:
  - 営業利益
  - 経常利益
  - 親会社株主に帰属する当期純利益
- Forecast and consensus require separate source-traced snapshots.

## Shared Rule

Connector output must satisfy the same `source_trace`, method, policy, confidence, and quality flag contract.
