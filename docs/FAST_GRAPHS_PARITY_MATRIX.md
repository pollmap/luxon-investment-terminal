# FAST Graphs Public-Reference Parity Matrix

This project targets functional and workflow parity with FAST Graphs-style fundamental valuation research while using our own code, visual system, data model, chart renderer, and source-traced calculation engine.

We do not copy proprietary FAST Graphs code, branding, logos, screenshots,
image assets, protected DOM/content, or private data. Reference research is
limited to manual review of public documentation and public pages.

## Reference Sources

- FAST Graphs Help Center, Historical Graph: https://docs.fastgraphs.com/en/articles/9419962-historical-graph
- FAST Graphs Help Center, Forecasting: https://docs.fastgraphs.com/en/articles/9436522-forecasting
- FAST Graphs Help Center, Forecasting Charts User Guide: https://docs.fastgraphs.com/en/articles/13577168-forecasting-charts-user-guide
- FAST Graphs Help Center, Navigating FAST Graphs: https://docs.fastgraphs.com/en/articles/9421089-navigating-fast-graphs
- FAST Graphs Help Center, Getting Started: https://docs.fastgraphs.com/en/articles/9354187-getting-started
- FAST Graphs Help Center, Analyst Scorecard: https://docs.fastgraphs.com/en/articles/9436633-analyst-scorecard-earnings-revisions-analyst-sentiment
- FAST Graphs Help Center, Screening Explained: https://docs.fastgraphs.com/en/articles/9354273-screening-explained
- FAST Graphs Help Center, Advanced Portfolios Explained: https://docs.fastgraphs.com/en/articles/9485044-advanced-portfolios-explained
- Public reviews and tutorials may be secondary references for discoverability
  and workflow expectations, never for protected assets or data definitions.

## Implementation Rules

- Feature parity is acceptable; proprietary asset copying is not.
- Numbers must come from filings, first-party releases, source-backed APIs, user-entered assumptions, or deterministic formulas.
- Forecast views may use consensus snapshots when available, but user-entered and deterministic scenarios must remain first-class.
- Every production metric must keep source trace, formula, method, confidence, policy, and quality flags.
- Public screenshots/videos can influence control placement and workflow coverage, but the final UI must remain our own terminal design.

## Historical Graph Parity

| Feature | Reference behavior | Current status | Next implementation |
| --- | --- | --- | --- |
| Black price line | Price over time overlaid on fundamentals | Implemented | Add weekly/monthly source-backed price granularity controls |
| Green earnings area | Fundamental metric area | Implemented | Add explicit metric scaling explanation in Graph Key |
| Orange fair value line | Growth-driven fair value multiple | Implemented | Add formula variant selector and audit row |
| Blue normal multiple line | Normal P/E over selected timeframe | Implemented with fiscal-year range and 1FY-20FY normal window selectors | Add daily/monthly source-backed granularity |
| Current valuation line | Current P/E/multiple applied across history | Implemented in web + SVG/PNG renderer | Add Graph Key value and export audit row |
| Dividend floor / payout | Dividend visual series | Implemented floor, payout ratio, and dividend yield toggles | Add source-backed dividend declaration stack |
| Forecast area | Future estimates on right side | Implemented for 1-5Y | Connect source-backed consensus tables |
| Recession shading | Gray event/recession bands | Implemented for US FRED USREC | Add KR/JP event calendars |
| Performance click calculation | Pick points and show annualized return | Implemented in browser chart for two selected fiscal years | Add persisted point-pair replay and source trace export |
| Series Key toggles | Add/remove every visible series | Implemented for main valuation, custom ratio, payout ratio, dividend yield, and recession bands | Extend to future event layers |
| Buy/sell dots | Portfolio transactions on historical graph | Implemented | Add average cost line |
| Timeframe bars/slider | 1Y-20Y and custom range | Implemented for fiscal-year MAX/5Y/3Y/1Y/custom range | Add graphical brush slider and daily/monthly cutoff |

## Forecasting Parity

| Calculator | Reference behavior | Current status | Next implementation |
| --- | --- | --- | --- |
| Estimates | 1-3Y consensus estimates and analyst count | Partial fixture/source interface plus low/median/high case matrix and direct audit links | Add real consensus ingestion and point-in-time snapshots |
| Normal Multiple | Apply historical normal multiple to estimates | Implemented with 1FY-20FY dropdown and chart-run/export persistence | Add source-backed estimate snapshots |
| LT Growth | 3-5Y long-term growth trendline | Implemented as mode scaffold | Add source-backed LT growth snapshots |
| Historical CAGR | Project from selected historical CAGR | Implemented deterministic mode | Add selectable CAGR windows |
| Custom | User growth, EPS, dividend, target multiple inputs | Implemented with Data Audit context propagation | Add per-year dividend overrides |
| Calculation lines | Multiple valuation lines around center case | Implemented 11 scenario lines plus FY1-FY5 return calculator table and direct return audit links | Add point-click chart return replay |
| High/Median/Low | Consensus case toggle | Implemented as case selector plus case matrix with audit links | Source-back with consensus vendors/snapshots |
| Estimate period selection | Current, 1M, 3M, 6M estimate revision snapshots | Partial through forecast evidence model | Store point-in-time estimate snapshots and tolerant date matching |

## Analyst Scorecard Parity

| Feature | Reference behavior | Current status | Next implementation |
| --- | --- | --- | --- |
| Earnings revisions | FE1/FE2/FE3 revision trend over 12 months | Partial | Store consensus snapshots by as-of date |
| Analyst sentiment | Latest, 1M, 3M, 12M estimate changes | Partial | Source-backed sentiment rows |
| Scorecard | Current, 1Y, 2Y locked estimates vs actuals | Partial | Begin snapshot collection now; avoid fake backfill |
| Beat/Hit/Miss | Margin-based classification | Implemented formula scaffold | Source-backed scorecard audit |

## Platform Parity

| Area | Reference behavior | Current status | Next implementation |
| --- | --- | --- | --- |
| Summary | Company facts and quick indicators | Implemented | Expand company info with TEV, credit rating, external links |
| Financials | Full statements and ratios | Implemented foundation | Source-backed statement depth for US/KR/JP |
| FUN Graphs | Financial underlying numbers charting | Implemented foundation | Add more statement metrics and quarterly mode |
| Fiscal Fitness | Ratio dashboard | Implemented foundation | Add peer/sector percentile context |
| Health Check / Score | Quality score axes | Implemented foundation | Formalize scoring book |
| Screening | General/Historic/Estimated/custom filters | Implemented foundation | Save screens and add estimated-source filters |
| Portfolio | Transactions, XIRR, allocation, income | Implemented foundation | Add income calendar and benchmark comparison |
| Export | Chart/report exports | Implemented SVG/PNG/MD/JSON/CSV | Add PDF report bundle |

## Public-reference workflow scope

The public Help Center shows the following product surfaces as first-class
research workflows. LUXON uses this list only as an independent implementation
checklist:

- App shell: left rail, global search, user/account menu, notification surface,
  and security header.
- Security tabs: Summary, Historical, Performance, Forecasting, Fun graphs,
  Fiscal fitness, FG Scores, Financials, and ETF Holdings.
- Historical controls: metric selector, Smart Metric, MAX-to-1Y period buttons,
  period dropdown, custom date toggle, high/low strip, fiscal EPS/Chg/Div table,
  range/legend controls, and facts/key/company/scorecard rail.
- Forecasting modes: Estimates, Normal Multiple, LT Growth, Historic CAGR, and
  Custom.
- Financials sub-tabs: Income statement, Balance sheet, CashFlow statement, and
  Ratios.

LUXON maps these workflows to source-traced components and its own design
tokens. Authenticated capture, automated scraping, and third-party product
assets are not part of the repository workflow.

## Current Priority Queue

1. Finish the KR-first `005930.KS` source-backed E2E path:
   OpenDART/pykrx/marcap raw -> normalized facts -> valuation map -> web ->
   Data Audit.
2. Finish Historical Graph parity controls: graphical brush slider,
   daily/monthly granularity, and tighter Series Key parity.
3. Complete Forecasting parity: Current/1M/3M/6M estimate snapshots,
   per-year custom EPS/dividend overrides, selectable CAGR windows, and 1Y-5Y
   user-input scenarios.
4. Replace fixture forecast evidence with real consensus snapshot ingestion
   where sources are available.
5. Add point-in-time estimate storage for Analyst Scorecard.
6. Expand source-backed KR/US/JP facts into valuation-ready Financials, FUN
   Graphs, Fiscal Fitness, Screening, Portfolio, and Data Audit rows.
