# 2026-09-27 continuation — verification and remaining gates

## Implemented in this change
- Stock heading scrolls with the document and no longer covers the chart.
- Phone navigation: market, screener, watchlist, more. More/settings share one dialog lifecycle.
- Favorite undo restores its prior groups; preferred initial view remains device-local.
- Per-stock chart mode/period/type preferences with explicit link values taking priority.
- Touch chart point selection and previous/next point controls.
- Summary/activity/fundamentals/company/filings views preserve the old full-detail API.
- History survives summary refresh; static-section ETags do not expire on quote-only changes.
- Shared server quote reader checks database revisions every 250 ms; per-stock sequences and process epoch accompany snapshots. This is worker-to-browser propagation, not proof of a provider's live tick rate.
- Browser subscribers reconnect, pause while hidden, restore via REST, reject repeated sequence numbers, and protect against an older in-flight REST response replacing an observed stream event.
- KIS ten-level book and reported executions have separate parsers, statuses and UI. On failure last-good rows remain; timestamps are not advanced for failed sections. Zero and absent quantities differ.
- Empty environment entries no longer hide existing key-file values. The PowerShell launcher follows the same nonempty-environment-first rule.
- DART detailed statement plan includes five completed business years plus the current year. Disclosure collection traverses all one-year pages in bounded chunks, persists a cursor, preserves receipts and raw evidence across interruptions.
- TypeScript source discovery excludes historical standalone build copies; current generated route types are included explicitly.

## Measured evidence
Local API 8217, stored Samsung data, one request per view, decoded JSON size (not compressed wire bytes):
| View | Bytes | One observed response |
|---|---:|---:|
| Legacy full | 1,044,451 | 168 ms |
| Summary | 44,983 | 6 ms |
| Activity | 32,794 | 4 ms |
| Company | 376,246 | 12 ms |
These samples are not a paired latency benchmark or whole-service 10x result. Summary is about 95.7% smaller; later tabs still fetch their own data.

Backend focused suite: 96 tests passed (public API, detail cache, stream recovery, book/execution parsing, filing pagination, financial backfill, financing, exploration, short selling, release runner). Frontend final browser suite: 33 passed, zero failed; recorded in market-browser-results.json. Production build (.next-completion-v6) passed. Changed-component ESLint was checked separately. Local WebSocket proxy connected and reconnected successfully against stored observations; see stream-local-20260927.json.

## Not complete
- Actual broker credentials are absent from the inspected worker environment/file. DART key is present. Never print values.
- Live intraday provider receipt, order-book/trade receipt, forced provider reconnect during a trading session remain unverified. This change does not manufacture them from fixtures.
- TTM remains unimplemented. DART whole-statement fields distinguish a three-month IS/CIS amount from cumulative amounts but do not establish all required period-start/end metadata. Do not sum balance-sheet accounts, ratios or unverified fiscal periods.
- Five-year collection plan is not five-year collection completion. Full security × dataset × period coverage and exceptions still need auditing and backfill.
- Public deployment blocked before authentication: ssh connect to 62.171.141.206 port 22: Permission denied. Evidence: work/releases/20260927-completion-v6-inspect/attempt.json. No remote mutation occurred.
- Actual father usability evaluation, installed PWA lifecycle/offline recovery, production 100-user p95 and mobile vitals, and whole-product 10x improvement remain unverified.

## Local operation
This turn started the new API on 8217 and a separate preview on 3217; old 3216 was preserved. Run the existing launcher with matching ports/build when restarting. Source collection already running elsewhere was not duplicated; restart the owned worker with this source version before treating new collectors as operational.

## Verified provider contracts
- KIS book: https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/inquire_asking_price_exp_ccn.py
- KIS executions: https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_llm/domestic_stock/inquire_time_itemconclusion/inquire_time_itemconclusion.py
- OpenDART whole statements: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020

Release source package verified: work/releases/20260927-completion-v6/source.tgz; SHA256 9963139f8c262a3dc1fa62008721148ae6b34a376dc8ac292932d42ed767e54f. No deployment performed.
