# Formula Book

## Adjusted Earnings

```txt
Adjusted Net Income
  = GAAP Net Income to common
    + after-tax add-backs
    - discontinued operations
    +/- direct tax adjustments

Adjusted EPS
  = Adjusted Net Income / Diluted Weighted Average Shares
```

S1에서는 회사가 공시한 `company_adjusted_eps`를 우선 저장하고, 재구성 EPS와 비교합니다. 허용오차를 넘으면 warning과 confidence penalty를 기록합니다.

## Tax Effect

```txt
after_tax_impact = sign * abs(pretax_amount) - sign * abs(tax_effect)
```

- explicit tax effect가 있으면 그대로 사용합니다.
- net-of-tax amount는 재과세하지 않습니다.
- tax effect가 없으면 effective tax rate를 사용하고 이상치일 때 statutory fallback을 씁니다.
- goodwill impairment는 기본적으로 no-tax-benefit gross add-back으로 처리합니다.

## YoY

```txt
yoy_pct = (current_metric - previous_metric) / abs(previous_metric) * 100
```

이전 값이 없거나 0이면 `null`입니다.

## EPS CAGR

```txt
cagr_pct = ((end_metric / start_metric) ** (1 / years) - 1) * 100
```

시작값, 종료값, 기간이 유효하지 않으면 0으로 처리합니다.

## Fair Value Multiple

```txt
fair_multiple = clamp(max(15, metric_cagr_pct), 8, 30)
fair_value_price = metric * fair_multiple
```

MVP 공식입니다. 향후 sector-specific GDF/P-E=G 보간식을 추가할 수 있습니다.

## Normal Multiple

```txt
normal_multiple = trimmed_mean(price / metric, trim_ratio=10%)
normal_value_price = metric * normal_multiple
```

선택 기간이 바뀌면 다시 계산합니다.

## Metric Selector

```txt
gaap_diluted_eps = reported diluted EPS
adjusted_operating = adjusted_eps from S1/S2/S3/S4 waterfall
basic_eps = source-backed basic EPS fact; unavailable in fixture mode
diluted_eps = source-backed diluted EPS fact; falls back to metric_values when adjusted_earnings is absent
revenue_share = revenue_reported * statement_scale / diluted_shares
sales_share = revenue_reported * statement_scale / diluted_shares
operating_cash_flow_share = operating_cash_flow_reported / diluted_shares
fcf_share = (operating_cash_flow_reported - abs(capex_reported)) / diluted_shares
ebitda_share = (operating_income_reported + depreciation_depletion_amortization_reported) / diluted_shares
ebit_share = operating_income_reported / diluted_shares
ffo_affo = REIT-only fixture proxy until source FFO/AFFO reconciliation is ingested
smart_metric = sector rule-selected metric; source-backed rule table required
```

`statement_scale`는 fixture 원천 단위에 따라 정합니다. US/JP fixture financials는 millions, KR fixture financials는 billions로 계산합니다. FFO/AFFO는 REIT 전용이며 production에서는 보도자료 또는 공시 리콘실리에이션에서 직접 적재해야 합니다.

## Forecast

MVP forecast horizon is clamped to 1-5 fiscal years. Forecast rows are estimates and must be separated from historical rows with `forecast_flag = true`.

```txt
metric_t = metric_0 * (1 + annual_growth_rate) ** t
target_price_t = metric_t * target_multiple
price_cagr = CAGR(start_price, target_price_t, t)
total_return_cagr = CAGR(start_price, target_price_t + cumulative_dividend, t)
margin_of_safety_pct = ((target_price_t - start_price) / target_price_t) * 100
```

Expanded return calculator formulas:

```txt
forecast_year = fiscal_year_t - latest_historical_fiscal_year
cumulative_dividend_t = annual_dividend_t * forecast_year
price_cagr_pct_t = (((target_price_t / start_price) ** (1 / forecast_year)) - 1) * 100
total_return_cagr_pct_t =
  ((((target_price_t + cumulative_dividend_t) / start_price) ** (1 / forecast_year)) - 1) * 100
margin_of_safety_pct_t = ((target_price_t - start_price) / target_price_t) * 100
```

Data Audit fact mapping:

```txt
forecast.price = target_price_t
forecast.price_cagr_pct = price_cagr_pct_t
forecast.total_return_cagr_pct = total_return_cagr_pct_t
forecast.margin_of_safety_pct = margin_of_safety_pct_t
forecast_snapshot.{low|median|high|current}.estimate_eps = point-in-time estimate EPS
forecast_case.{low|median|high}.target_price = estimate_eps_case * target_multiple
forecast_case.{low|median|high}.price_cagr_pct =
  (((target_price_case / start_price) ** (1 / forecast_year)) - 1) * 100
forecast_case.{low|median|high}.total_return_cagr_pct =
  ((((target_price_case + annual_dividend * forecast_year) / start_price)
    ** (1 / forecast_year)) - 1) * 100
forecast_case.{low|median|high}.margin_of_safety_pct =
  ((target_price_case - start_price) / target_price_case) * 100
```

`forecast.price_cagr_pct`, `forecast.total_return_cagr_pct`, and
`forecast.margin_of_safety_pct` must carry
`source_trace.calculation_inputs` with at least `start_price`, `target_price`,
`forecast_year`, and, for total return, `annual_dividend`. The input ledger must
also include `start_price_trace` for the price input and `dividend_trace` for
the dividend input when dividend-inclusive return is calculated. If a consensus EPS
snapshot is missing for a forecast year, the row must use a deterministic
fallback trace for that fiscal year rather than inheriting the previous
snapshot's period or document id.

`forecast_case.*` rows are a one-year bear/base/bull comparison derived from
the low/median/high forecast snapshot, selected target multiple, latest
historical start price, and latest dividend input. Their `source_trace` must
include `forecast_snapshot_trace`, `forecast_assumption_trace`,
`start_price_trace`, and `calculation_inputs`.

Forecast source는 `consensus_snapshot`, `deterministic_trend`, `user_input`, `ai_assisted_review` 중 하나로 표시합니다. MVP에서 consensus는 fixture preset이며 production 데이터가 아닙니다.

Forecast row도 일반 historical row와 동일하게 `source_trace`를 가져야 합니다. MVP fixture forecast는 `source_document_id`, `filing_id`, `period`, `unit`, `currency`, `formula`, `quality_status`를 포함하고 `fixture_non_production_forecast`로 표시합니다.

When Postgres contains point-in-time `consensus_estimate_snapshots`, Estimates and
Normal Multiple forecast modes use the stored EPS snapshot values by fiscal year.
The projection still exposes `forecast_source = consensus_snapshot`. Missing
forecast years are explicitly listed in `source_trace.missing_consensus_years` and
are filled only by the deterministic growth continuity formula:

```txt
if consensus_eps_t exists:
  metric_t = consensus_eps_t
else:
  metric_t = metric_(t-1) * (1 + consensus_or_implied_growth)
```

The production source coverage gate treats the 1Y-5Y forecast path as ready only
when each required fiscal year has an `adjusted_operating_eps` snapshot with
`estimate_case = median` or `estimate_case = current`. Low/high cases are
scenario evidence, not the base valuation EPS path.

Data Audit also exposes forecast configuration as `forecast_assumption.*` rows.
These rows cover mode, case, growth rate, target multiple, analyst count, manual
EPS override count, formula, and source. The values come from the valuation-map
forecast metadata and keep the same `source_trace` contract as chart datapoints.

`ai_review` forecast mode does not let an LLM create EPS, target prices, or
returns. It uses a deterministic review blend:

```txt
review_growth_rate_pct = (historical_cagr + consensus_growth_rate) / 2
metric_t = metric_0 * (1 + review_growth_rate_pct)^t
target_price_t = metric_t * target_multiple
```

The resulting `source_trace` must include `llm_generated_numbers=false`,
`ai_role=commentary_only`, the upstream metric/consensus traces, and the
deterministic formula above.

## Consensus Snapshot And Scorecard

Production consensus forecast는 `consensus_estimate_snapshots`에서 snapshot date 기준으로 읽습니다. 같은 fiscal year에 여러 snapshot이 있으면 revision ledger는 시간순으로 유지하고, forecast case는 최신 snapshot의 `low`, `median`, `high` 값을 사용합니다.

```txt
revision_delta = current_estimate_eps - previous_estimate_eps
estimate_error_pct = (estimate_eps / actual_adjusted_eps - 1) * 100
scorecard_hit = abs(estimate_error_pct) <= tolerance_pct
hit_rate_pct = hit_count / scored_count * 100
```

Analyst Scorecard는 실제 `adjusted_earnings.adjusted_eps`와 과거 point-in-time snapshot이 모두 있는 연도만 산출합니다. 1Y prior는 `period_end - 365 days`, 2Y prior는 `period_end - 730 days` 이전에 수집된 가장 가까운 snapshot을 사용합니다. 과거 snapshot이 없으면 hit-rate를 임의로 백필하지 않고 `pending_actual_overlap` 또는 fixture/non-production 상태로 표시합니다.

## Analyst Scorecard

The dedicated Analyst Scorecard API exposes point-in-time estimate accuracy as
source-traced facts:

```txt
estimate_error_pct = (point_in_time_estimate_eps / actual_adjusted_eps - 1) * 100
result_1y = hit if abs(error_1y_pct) <= 10 else miss
result_2y = hit if abs(error_2y_pct) <= 20 else miss
hit_rate_1y_pct = hit_1y_count / scored_1y_count * 100
hit_rate_2y_pct = hit_2y_count / scored_2y_count * 100
```

Rows without the required 1Y or 2Y snapshot remain `not_available`.
Production hit rates are not backfilled from current consensus estimates.

## Portfolio

```txt
signed_quantity = buy_qty - sell_qty
market_value = signed_quantity * latest_price
weight_pct = position_market_value / total_market_value * 100
XIRR = rate where NPV(dated_cashflows, rate) = 0
```

Portfolio CSV imports are explicit user-provided source inputs. `import_trace`
records the import source, row count, quality status, and deterministic formula.

## Performance

```txt
shares_purchased = initial_investment / start_price
ending_value = shares_purchased * end_price
dividends_received = sum(dividend_per_share_t * shares_purchased)
capital_gain = ending_value - initial_investment
total_gain = ending_value + dividends_received - initial_investment
price_return_pct = capital_gain / initial_investment * 100
dividend_return_pct = dividends_received / initial_investment * 100
total_return_pct = total_gain / initial_investment * 100
annualized_total_return_pct =
    ((ending_value + dividends_received) / initial_investment) ** (1 / years) - 1
```

Performance rows are derived from historical valuation-map rows only. Forecast
rows are excluded. Missing dividend source rows are flagged instead of being
silently treated as real zero dividends.

## Screener

The Screener is a deterministic classification layer over source-backed or
fixture snapshot rows.

```txt
metric_to_value =
    per <= max_per
  AND roe >= min_roe when provided
  AND eps_cagr >= min_eps_cagr when provided
  AND debt_to_equity <= max_debt_to_equity when provided
  AND market_cap >= min_market_cap when provided
  AND market_cap_usd >= min_market_cap_usd when provided

metric_to_metric =
    true if require_roe_gt_roic is false
    else roe > roic

relative_threshold = normal_pe * (1 - relative_discount_pct / 100)
company_relative = per <= relative_threshold

passes_all = metric_to_value AND metric_to_metric AND company_relative
```

Missing required values fail the relevant active filter. Missing optional values
are ignored only when the corresponding threshold is not provided.

For cross-market size filters:

```txt
market_cap_usd =
    market_cap
    if local currency is USD

market_cap_usd =
    market_cap / local_currency_per_usd_fx_rate
    if local currency is KRW or JPY and source-backed FX is available
```

The default FX inputs are FRED `DEXKOUS` and `DEXJPUS`, which are local currency
per 1 USD observations. If the FX source trace is missing, `market_cap_usd`
stays unavailable and any active `min_market_cap_usd` filter fails for that row.

## Fun Graphs

```txt
fun_graph_point(metric_key, fiscal_year) = selected source-traced financial row field
```

FUN Graphs do not create new financial facts. They expose Financial Underlying
Numbers as line series using already-normalized `financials` rows:

- `revenue`
- `adjusted_eps`
- `gaap_eps_diluted`
- `free_cash_flow`
- `gross_margin_pct`
- `operating_margin_pct`
- `net_margin_pct`
- `roe_pct`
- `roic_pct`
- `debt_to_equity`

The browser line chart normalizes each selected metric only for screen
coordinates. The actual table and audit values remain the source values. Every
point carries `source_trace`, `method`, `confidence`, `quality_status`, and
`flags`.

## Use Of Cash

```txt
fcf_margin_pct = free_cash_flow / revenue * 100
dividend_payout_pct = dividend_per_share / eps * 100
```

The engine does not infer cash-use buckets. `operating_cash_flow`, `capex`,
`dividends_paid`, `share_repurchases`, `debt_repayment`, `acquisitions`, and
`net_cash_use` remain `null` until source-backed facts are ingested. Missing
facts are exposed through flags such as `missing_capex_source` and
`missing_share_repurchases_source`.

Missing dividends are not treated as zero. A zero dividend is used only when a
source row explicitly reports zero.

## Fiscal Fitness

```txt
fcf_margin_pct = free_cash_flow / revenue * 100
revenue_growth_pct = (revenue - previous_revenue) / abs(previous_revenue) * 100
eps_growth_pct = (eps - previous_eps) / abs(previous_eps) * 100
```

Direct source metrics are read from normalized financial rows: gross margin,
operating margin, net margin, ROE, ROIC, and debt/equity. Current ratio, quick
ratio, and interest coverage are not inferred. They remain `null` with
missing-source flags until current assets, current liabilities, cash, inventory,
EBIT, and interest expense facts are ingested.

CSV 거래내역은 `date,ticker,side,quantity,price,currency,sector` 헤더를 요구합니다.
## Health Check

```txt
axis_metric_score = clamp((value - poor_threshold) / (excellent_threshold - poor_threshold) * 100, 0, 100)
lower_is_better_score = clamp((poor_threshold - value) / (poor_threshold - excellent_threshold) * 100, 0, 100)
axis_score = average(scored_axis_inputs)
overall_score =
    profitability * 0.25
  + cash_generation * 0.20
  + financial_strength * 0.20
  + growth * 0.20
  + predictability * 0.15
```

The MVP Health Check is an FG Score-style derived quality layer. It does not
create new financial facts. Inputs come from Fiscal Fitness rows and
forecast/scorecard evidence. Missing axis inputs are flagged. If predictability
lacks point-in-time consensus snapshots, it uses a neutral 50.00 score and
emits `predictability_requires_point_in_time_consensus_snapshots`.

Rating bands:

```txt
strong  >= 80
healthy >= 65
mixed   >= 50
watch   <  50
```

## Research Report

The Research Report is a deterministic assembly layer. It does not create new
financial facts and does not use an LLM to generate numbers.

Inputs:

- valuation-map latest row
- Health Check overall score and axis evidence
- Fiscal Fitness rows
- forecast evidence and scenario rows
- Use Of Cash rows

Core valuation classification:

```txt
valuation_gap_pct = (latest_price / fair_value_price - 1) * 100

premium_to_fair_value if valuation_gap_pct > 25
discount_to_fair_value if valuation_gap_pct < -15
near_fair_value otherwise
```

Forecast evidence uses the existing Forecast formulas:

```txt
forecast_total_return_cagr_pct = total_return_cagr from the selected forecast case
```

Capital-allocation classification:

```txt
dividend_supported if dividend_payout_pct <= 70
dividend_requires_review if dividend_payout_pct > 70
cash_use_source_incomplete if required source facts are missing
```

Report-level audit facts:

- `research_report.valuation_gap_pct`
- `research_report.health_score`
- `research_report.forecast_total_return_cagr_pct`
- `research_report.section_count`

Each report section and audit fact carries `source_trace`, `formula`,
`method = research_report_derived`, `policy = research_report`, and
`quality_status`.

## Export Center

Exports are serialization contracts, not calculation layers.

```txt
research_report_markdown = render(report + data_audit) as Markdown
research_report_json = serialize(manifest + report + data_audit) as JSON
data_audit_csv = flatten(data_audit rows + source_trace fields) as CSV
```

No export endpoint creates, estimates, or backfills financial values. The source
trace, formula, method, policy, quality status, and flags from the upstream
payload must remain visible in the exported file.
