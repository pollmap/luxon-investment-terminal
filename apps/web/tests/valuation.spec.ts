import { expect, test } from "@playwright/test";

test("KR priority source-required API payload keeps terminal out of live state", async ({ page }) => {
  await page.route("**/api/v1/companies/005930.KS/valuation-map**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [],
        meta: {
          ticker: "005930.KS",
          surface: "valuation_map",
          data_mode: "source_backed_required",
          data_backend: "postgres_required",
          quality_status: "missing_source_backed_data",
          financial_numbers_allowed: false,
          source_note: "KR priority coverage requires OpenDART/pykrx/marcap source-backed rows.",
          source_trace: {
            source_document_id: "nexus-kr-top-market-cap-priority-universe-v2",
            source_type: "product_priority_universe_contract",
            filing_id: "NEXUS-KR-TOP-MARKET-CAP-PRIORITY-V2",
            period: "initial_coverage",
            unit: "ticker_list",
            currency: "N/A",
            method: "source_backed_required_gate",
            formula: "fixture financial values are blocked until source-backed rows are loaded",
            quality_status: "missing_source_backed_data",
            quality_flags: ["fixture_fallback_blocked_for_kr_priority"],
            financial_numbers_allowed: false
          }
        }
      })
    });
  });

  await page.goto("/");

  await expect(page.locator(".status-pill")).toHaveText("source required");
  await expect(page.getByTestId("company-header-panel")).toContainText("source_backed_required");
  await expect(page.getByTestId("company-source-gate")).toContainText("KR E2E source gate");
  await expect(page.getByTestId("company-source-gate")).toContainText("OpenDART + pykrx + marcap");
  await expect(page.getByTestId("company-source-gate")).toContainText("no source_trace, no number");
  await expect(page.getByTestId("historical-source-lock")).toContainText("Chart waits for source-traced rows");
  await expect(page.getByTestId("historical-source-lock")).toContainText("OpenDART + pykrx + marcap");
  await expect(page.getByTestId("historical-source-lock")).toContainText("no source_trace, no number");
});

test("KR valuation-map source-backed cache unlocks the historical map", async ({ page }) => {
  const trace = {
    source: "opendart_pykrx_cache",
    source_document_id: "playwright-kr-cache-source-backed-contract",
    source_type: "opendart_pykrx_cache",
    filing_id: "PLAYWRIGHT-KR-CACHE-005930",
    period: "2024-12-31",
    unit: "KRW per share",
    currency: "KRW",
    method: "company_reported",
    formula: "contract payload for source_backed_cache rendering",
    quality_status: "source_backed_cache",
    quality_flags: ["playwright_contract_payload"]
  };
  const marketGapFactId = "005930.KS-2022-data_quality.kr_market_gap.source_no_rows_before_first_trade";
  const financialGapFactId = "005930.KS-2022-data_quality.kr_financial_gap.source_no_data";
  const gapTrace = {
    ...trace,
    source_type: "kr_cache_market_gap_diagnostic",
    source_document_id: "kr-cache:005930.KS:2022:market-gap:source_no_rows_before_first_trade",
    filing_id: "kr-cache:005930.KS:2022:market-gap:source_no_rows_before_first_trade",
    period: "FY2022",
    unit: "diagnostic",
    method: "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
    formula: "diagnostic = KR cache price and market-structure coverage check",
    quality_status: "source_no_rows_before_first_trade",
    quality_flags: ["kr_cache_gap_diagnostic", "kr_market_gap_source_no_rows_before_first_trade"]
  };

  await page.route("**/api/v1/companies/005930.KS/valuation-map**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            fiscal_year: 2023,
            period_end: "2023-12-31",
            metric: "6500",
            price: "73000",
            normal_multiple: "12",
            fair_multiple: "15",
            fair_value_price: "97500",
            forecast_flag: false,
            dividend: "1444",
            eps_yoy: "8.3",
            source_trace: { ...trace, period: "2023-12-31", filing_id: "PLAYWRIGHT-KR-CACHE-005930-2023" }
          },
          {
            fiscal_year: 2024,
            period_end: "2024-12-31",
            metric: "9000",
            price: "81000",
            normal_multiple: "12.5",
            fair_multiple: "15",
            fair_value_price: "135000",
            forecast_flag: false,
            dividend: "1444",
            eps_yoy: "38.5",
            source_trace: trace
          },
          {
            fiscal_year: 2025,
            period_end: "2025-12-31",
            metric: "9900",
            price: "99000",
            normal_multiple: "12.5",
            fair_multiple: "15",
            fair_value_price: "148500",
            forecast_flag: true,
            dividend: "1500",
            eps_yoy: "10",
            source_trace: {
              ...trace,
              period: "2025-12-31",
              method: "deterministic_forecast",
              formula: "metric = prior_year_metric * (1 + user_growth_rate)"
            }
          }
        ],
        meta: {
          ticker: "005930.KS",
          surface: "valuation_map",
          data_mode: "source_backed_cache",
          data_backend: "kr_valuation_input_cache",
          quality_status: "source_backed_cache",
          cache_path: "storage/cache/kr-valuation-inputs/005930.KS.json",
          kr_cache: {
            data_mode: "source_backed_cache",
            data_backend: "kr_valuation_input_cache",
            financial_numbers_allowed: true,
            cache_status: "ok",
            coverage_status: "partial_source_backed",
            full_coverage_ready: false,
            valuation_ready: true,
            coverage_years: {
              price: [2023, 2024],
              market_structure: [2023, 2024],
              financial_metric: [2024],
              valuation_points: [2023, 2024]
            },
            missing_years: {
              market_input: [2022],
              financial_metric: [2022]
            },
            market_gap_diagnostics: [
              {
                fiscal_year: 2022,
                status: "source_no_rows_before_first_trade",
                reason: "No pykrx or marcap rows exist for this ticker before the first cached market row 2023-12-31.",
                next_action: "keep_partial_market_history_start",
                missing_price: true,
                missing_market_structure: true,
                first_available_market_date: "2023-12-31"
              }
            ],
            financial_gap_diagnostics: [
              {
                fiscal_year: 2022,
                status: "source_no_data",
                reason: "OpenDART returned a non-success status for this annual filing request.",
                next_action: "keep_partial_or_add_alternate_source",
                opendart_status: "013",
                row_count: 0
              }
            ],
            quality_flags: ["partial_valuation_coverage", "missing_financial_metric_2022"]
          },
          source_trace: trace,
          price_points: [
            {
              date: "2023-12-31",
              fiscal_year: 2023,
              close_price: "73000",
              currency: "KRW",
              frequency: "annual_source_cache",
              source_trace: trace
            },
            {
              date: "2024-12-31",
              fiscal_year: 2024,
              close_price: "81000",
              currency: "KRW",
              frequency: "annual_source_cache",
              source_trace: trace
            }
          ],
          forecast: {
            base_year: 2024,
            forecast_years: 1,
            source: "deterministic_user_input",
            source_trace: trace
          }
        }
      })
    });
  });
  await page.route("**/api/v1/companies/005930.KS/data-audit**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            fact_id: marketGapFactId,
            fact_name: "data_quality.kr_market_gap.source_no_rows_before_first_trade",
            value: "No pykrx or marcap rows exist for this ticker before the first cached market row 2023-12-31.",
            fiscal_year: 2022,
            method: "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
            policy: "data_quality",
            confidence: "0.90",
            quality_status: "source_no_rows_before_first_trade",
            flags: ["kr_cache_gap_diagnostic", "kr_market_gap_source_no_rows_before_first_trade"],
            formula: "diagnostic = KR cache price and market-structure coverage check",
            source_trace: gapTrace
          },
          {
            fact_id: financialGapFactId,
            fact_name: "data_quality.kr_financial_gap.source_no_data",
            value: "OpenDART returned a non-success status for this annual filing request.",
            fiscal_year: 2022,
            method: "KR_CACHE_FINANCIAL_GAP_DIAGNOSTIC",
            policy: "data_quality",
            confidence: "0.90",
            quality_status: "source_no_data",
            flags: ["kr_cache_gap_diagnostic", "kr_financial_gap_source_no_data"],
            formula: "diagnostic = KR cache OpenDART annual financial metric coverage check",
            source_trace: {
              ...gapTrace,
              source_type: "kr_cache_financial_gap_diagnostic",
              source_document_id: "opendart:005930.KS:2022:status:013",
              filing_id: "opendart:005930.KS:2022:status:013",
              method: "KR_CACHE_FINANCIAL_GAP_DIAGNOSTIC",
              formula: "diagnostic = KR cache OpenDART annual financial metric coverage check",
              quality_status: "source_no_data",
              quality_flags: ["kr_cache_gap_diagnostic", "kr_financial_gap_source_no_data"]
            }
          }
        ],
        meta: {
          ticker: "005930.KS",
          total: 2,
          data_mode: "source_backed_cache"
        }
      })
    });
  });
  await page.route("**/api/security/402340.KS/adjusted**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ticker: "402340.KS",
        policy: { base_policy: "street_comparable" },
        series: []
      })
    });
  });
  await page.route("**/api/v1/companies/402340.KS/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/data-audit")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: [
            {
              fact_id: "402340.KS-2020-data_quality.kr_market_gap.api_market_gap_ref",
              fact_name: "data_quality.kr_market_gap.api_market_gap_ref",
              value: "API-provided KR Top10 market gap reference.",
              fiscal_year: 2020,
              method: "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
              policy: "data_quality",
              confidence: "0.90",
              quality_status: "api_market_gap_ref",
              flags: ["kr_cache_gap_diagnostic", "api_market_gap_ref"],
              formula: "diagnostic = KR cache price and market-structure coverage check",
              source_trace: {
                ...gapTrace,
                source_document_id: "api:market-gap:402340.KS:2020",
                filing_id: "api:market-gap:402340.KS:2020",
                period: "FY2020",
                quality_status: "api_market_gap_ref",
                quality_flags: ["kr_cache_gap_diagnostic", "api_market_gap_ref"]
              }
            }
          ],
          meta: {
            ticker: "402340.KS",
            total: 1,
            data_mode: "source_backed_cache"
          }
        })
      });
      return;
    }
    if (path.endsWith("/valuation-map")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: [],
          meta: {
            ticker: "402340.KS",
            surface: "valuation_map",
            data_mode: "source_backed_required",
            data_backend: "kr_valuation_input_cache",
            financial_numbers_allowed: false,
            quality_status: "partial_source_backed",
            source_trace: {
              ...trace,
              source_document_id: "playwright-kr-cache-402340-required",
              filing_id: "PLAYWRIGHT-KR-CACHE-402340",
              quality_status: "partial_source_backed"
            }
          }
        })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: path.endsWith("/snapshot")
          ? {
              ticker: "402340.KS",
              name: "SK SQUARE",
              market: "KR",
              currency: "KRW",
              source_trace: {
                ...trace,
                source_document_id: "playwright-kr-cache-402340-snapshot",
                filing_id: "PLAYWRIGHT-KR-CACHE-402340-SNAPSHOT"
              }
            }
          : []
      })
    });
  });
  await page.route("**/api/source-documents/resolve**", async (route) => {
    const url = new URL(route.request().url());
    const sourceDocumentId = url.searchParams.get("source_document_id") ?? "";
    const isKrCache = sourceDocumentId.startsWith("kr-cache:");
    const isOpenDart = sourceDocumentId.startsWith("opendart:");
    const previewText = isOpenDart
      ? JSON.stringify({ status: "013", message: "No data for requested filing" }, null, 2)
      : JSON.stringify({ ticker: "005930.KS", coverage_status: "partial_source_backed" }, null, 2);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          source_document_id: sourceDocumentId,
          status: isKrCache || isOpenDart ? "found" : "logical_only",
          source: isKrCache ? "kr_valuation_input_cache" : isOpenDart ? "opendart" : "kr_cache_diagnostic",
          content_hash: isKrCache || isOpenDart ? "playwright-source-document-hash" : null,
          local_path: isKrCache
            ? "storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json"
            : isOpenDart
              ? "storage/raw/opendart/005930.KS/00126380-2022-11011-CFS.json"
              : null,
          source_url: null,
          filing_url: null,
          content_type: isKrCache || isOpenDart ? "application/json" : null,
          preview_available: isKrCache || isOpenDart,
          preview_text: isKrCache || isOpenDart ? previewText : null,
          resolver: isKrCache
            ? "local_kr_valuation_cache_logical_id"
            : isOpenDart
              ? "local_opendart_logical_id"
              : "logical_source_document_id",
          metadata: {
            note: isKrCache || isOpenDart
              ? "Resolved to stored source evidence."
              : "This source_document_id is a deterministic audit identifier."
          }
        }
      })
    });
  });
  await page.route("**/api/v1/system/kr-valuation-cache-coverage**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        market: "KR",
        data_backend: "kr_valuation_input_cache",
        data_mode: "source_backed_cache",
        coverage_status: "partial_source_backed",
        quality_status: "source_backed_cache_partial",
        summary: {
          tickers_expected: 10,
          cache_files_found: 10,
          valuation_ready: 10,
          complete: 6,
          partial_source_backed: 4,
          missing: 0,
          full_coverage_ready: 6,
          financial_numbers_allowed: 10
        },
        quality_flags: ["partial_valuation_coverage"],
        source_trace: {
          source: "kr_valuation_input_cache",
          source_type: "kr_valuation_cache_universe_summary",
          source_document_id: "kr-valuation-cache-universe-summary",
          filing_id: "KR-VALUATION-CACHE-UNIVERSE-SUMMARY",
          period: "latest_cache",
          unit: "ticker_coverage_count",
          currency: "KRW",
          method: "KR_VALUATION_CACHE_METADATA_SUMMARY",
          formula: "Count source-traced KR valuation input cache coverage.",
          quality_status: "source_backed_cache_partial",
          quality_flags: ["partial_valuation_coverage"]
        },
        rows: [
          {
            ticker: "005930.KS",
            status: "complete",
            years: [2020, 2021, 2022, 2023, 2024, 2025],
            missing: { market_input: [], financial_metric: [] }
          },
          {
            ticker: "000660.KS",
            status: "complete",
            years: [2020, 2021, 2022, 2023, 2024, 2025],
            missing: { market_input: [], financial_metric: [] }
          },
          {
            ticker: "402340.KS",
            status: "partial_source_backed",
            years: [2023, 2024, 2025],
            missing: { market_input: [2020], financial_metric: [2020, 2021, 2022] }
          },
          {
            ticker: "032830.KS",
            status: "partial_source_backed",
            years: [2023, 2024, 2025],
            missing: { market_input: [], financial_metric: [2020, 2021, 2022] }
          }
        ].map((row) => ({
          ticker: row.ticker,
          cache_found: true,
          valuation_ready: true,
          financial_numbers_allowed: true,
          full_coverage_ready: row.status === "complete",
          coverage_status: row.status,
          cache_status: "ok",
          cache_path: `storage/cache/kr-valuation-inputs/${row.ticker}-2020-2025-valuation-inputs.json`,
          valuation_years: row.years,
          missing_years: row.missing,
          market_gap_count: row.missing.market_input.length,
          financial_gap_count: row.missing.financial_metric.length ? 1 : 0,
          gap_audit_refs: [
            ...row.missing.market_input.map((year) => ({
              scope: "market",
              fiscal_year: year,
              status: "api_market_gap_ref",
              fact_name: "data_quality.kr_market_gap.api_market_gap_ref",
              fact_id: `${row.ticker}-${year}-data_quality.kr_market_gap.api_market_gap_ref`,
              label: `Market FY${year}`,
              source_document_id: `api:market-gap:${row.ticker}:${year}`,
              source_type: "kr_cache_market_gap_diagnostic",
              method: "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
              quality_status: "api_market_gap_ref"
            })),
            ...row.missing.financial_metric.map((year) => ({
              scope: "financial",
              fiscal_year: year,
              status: "api_financial_gap_ref",
              fact_name: "data_quality.kr_financial_gap.api_financial_gap_ref",
              fact_id: `${row.ticker}-${year}-data_quality.kr_financial_gap.api_financial_gap_ref`,
              label: `Metric FY${year}`,
              source_document_id: `api:financial-gap:${row.ticker}:${year}`,
              source_type: "kr_cache_financial_gap_diagnostic",
              method: "KR_CACHE_FINANCIAL_GAP_DIAGNOSTIC",
              quality_status: "api_financial_gap_ref"
            }))
          ],
          rejected_cache_points: 0,
          quality_flags: row.status === "complete" ? [] : ["partial_valuation_coverage"],
          source_note: "Playwright source-backed cache aggregate."
        }))
      })
    });
  });
  await page.route("**/api/v1/system/source-coverage**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "partial",
        data_mode: "source_backed_required",
        data_backend: "postgres",
        postgres: { reachable: true, error: null },
        requirements: {
          min_historical_years: 3,
          min_forecast_years: 5,
          consensus_forecast_required: true,
          core_required: ["security", "adjusted_earnings", "price_bars", "financial_metrics", "source_evidence"],
          consensus_forecast_optional: ["consensus_estimate_snapshots"]
        },
        summary: {
          tickers_expected: 10,
          core_ready: 2,
          consensus_forecast_ready: 1,
          missing_core: ["402340.KS", "032830.KS"],
          missing_consensus_forecast: ["000660.KS", "402340.KS", "032830.KS"],
          missing_by_requirement: {
            financial_metrics: ["402340.KS", "032830.KS"],
            consensus_forecast: ["000660.KS", "402340.KS", "032830.KS"]
          }
        },
        remediation: {
          status: "needs_source_data",
          years: "2020:2025",
          next_actions: [],
          notes: ["KR Top10 production gate requires persisted source coverage."]
        },
        tickers: [
          ["005930.KS", true, true, []],
          ["000660.KS", true, false, ["consensus_forecast"]],
          ["402340.KS", false, false, ["financial_metrics", "consensus_forecast"]],
          ["032830.KS", false, false, ["financial_metrics", "consensus_forecast"]]
        ].map(([ticker, coreReady, forecastReady, missing]) => ({
          ticker,
          name: String(ticker),
          market: "KR",
          country: "KR",
          currency: "KRW",
          pattern: "kr_top_market_cap",
          status: coreReady && forecastReady ? "ready" : "partial",
          core_ready: coreReady,
          consensus_forecast_ready: forecastReady,
          counts: {
            security: 1,
            adjusted_years: coreReady ? 3 : 1,
            price_years: coreReady ? 3 : 1,
            financial_metric_years: coreReady ? 3 : 0,
            consensus_valuation_years: forecastReady ? 5 : 0,
            consensus_valuation_snapshots: forecastReady ? 5 : 0
          },
          method_counts: { s1: 0, s2: 0, s4: 0 },
          available_metric_keys: coreReady ? ["adjusted_operating_eps"] : [],
          missing_required: missing
        }))
      })
    });
  });

  await page.goto("/");

  await expect(page.locator(".status-pill")).toHaveText("API live");
  await expect(page.getByTestId("company-header-panel")).toContainText("source_backed_cache");
  await expect(page.getByTestId("company-source-gate")).toContainText("Source-backed render path");
  await expect(page.getByTestId("historical-source-lock")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Historical Valuation Map" })).toBeVisible();
  await expect(page.getByTestId("historical-map-readout")).toContainText("Actual history");
  await expect(page.getByTestId("historical-readout-actual")).toContainText("2 rows");
  await expect(page.getByTestId("historical-readout-actual")).toContainText("2023-2024");
  await expect(page.getByTestId("historical-readout-forecast")).toContainText("1/1Y");
  await expect(page.getByTestId("historical-readout-forecast")).toContainText("2025E-2025E");
  await expect(page.getByTestId("historical-readout-source")).toContainText("3/3");
  await expect(page.getByTestId("historical-readout-source")).toContainText("all rows storage-ready");
  await expect(page.getByTestId("historical-readout-method")).toContainText("company_reported");
  await expect(page.getByTestId("historical-kr-source-contract")).toContainText("KR E2E Source Contract");
  await expect(page.getByTestId("historical-kr-source-contract-status")).toContainText("partial source backed");
  await expect(page.getByTestId("historical-kr-source-contract-numbers")).toContainText("allowed");
  await expect(page.getByTestId("historical-kr-source-contract-years")).toContainText("2023, 2024");
  await expect(page.getByTestId("historical-kr-source-contract-missing")).toContainText("market 2022 / metric 2022");
  await expect(page.getByTestId("historical-kr-source-contract-flags")).toContainText("partial_valuation_coverage");
  await expect(page.getByTestId("kr-cache-coverage")).toContainText("KR valuation cache");
  await expect(page.getByTestId("kr-cache-coverage-status")).toContainText("partial source backed");
  await expect(page.getByTestId("kr-cache-financial-numbers")).toContainText("Numbers allowed");
  await expect(page.getByTestId("kr-cache-quality-flags")).toContainText("partial_valuation_coverage");
  await expect(page.getByTestId("kr-cache-market-gap-diagnostics")).toContainText("source no rows before first trade 1");
  await expect(page.getByTestId("kr-cache-gap-diagnostics")).toContainText("source no data 1");
  await expect(page.getByTestId("kr-cache-universe")).toContainText("KR Top 10 valuation cache");
  await expect(page.getByTestId("kr-cache-universe-ready")).toContainText("10/10");
  await expect(page.getByTestId("kr-cache-universe-complete")).toContainText("6");
  await expect(page.getByTestId("kr-cache-universe-partial")).toContainText("4");
  await expect(page.getByTestId("kr-cache-universe-missing")).toContainText("0");
  await expect(page.getByTestId("kr-cache-universe-rows")).toContainText("402340.KS");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("Partial source-backed gap ledger");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("402340.KS");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("Market");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("missing 2020");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("Metric");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("missing 2020, 2021, 2022");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("alternate market and OpenDART source evidence");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("032830.KS");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("alternate OpenDART financial evidence");
  await expect(page.getByTestId("kr-top10-partial-gaps")).toContainText("cross-ticker");
  await expect(page.getByTestId("kr-top10-partial-gaps").getByRole("link", { name: "Open fact" }).first()).toHaveAttribute(
    "href",
    /\/api\/data-audit\/402340\.KS-2020-data_quality\.kr_market_gap\.api_market_gap_ref/
  );
  await expect(page.getByTestId("kr-top10-partial-gaps").getByRole("link", { name: "Open source doc" }).first()).toHaveAttribute(
    "href",
    /\/api\/source-documents\/resolve\?source_document_id=api%3Amarket-gap%3A402340\.KS%3A2020/
  );
  await expect(page.getByTestId("kr-top10-partial-gaps").getByRole("link", { name: "Open fact" }).nth(4)).toHaveAttribute(
    "href",
    /\/api\/data-audit\/032830\.KS-2020-data_quality\.kr_financial_gap\.api_financial_gap_ref/
  );
  await expect(page.getByTestId("kr-cache-universe-source-doc")).toContainText("kr-valuation-cache-universe-summary");
  await expect(page.getByTestId("kr-top10-completion-matrix")).toContainText("KR Top10 completion matrix");
  await expect(page.getByTestId("kr-top10-stage-grid")).toContainText("10/10");
  await expect(page.getByTestId("kr-top10-stage-grid")).toContainText("0/10");
  await expect(page.getByTestId("kr-top10-stage-grid")).toContainText("API allowed");
  await expect(page.getByTestId("kr-top10-stage-grid")).toContainText("2/10");
  await expect(page.getByTestId("kr-top10-stage-grid")).toContainText("1/10");
  await expect(page.getByTestId("kr-top10-production-gate")).toContainText("9 production DB/forecast gaps before deploy");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("402340.KS");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("cache ready");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("DB gaps 2");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("forecast pending");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("diagnostics 2");
  await expect(page.getByTestId("kr-top10-completion-command")).toContainText("pnpm e2e:source:kr:top10:local-dry-run");
  await expect(page.getByTestId("kr-top10-completion-command")).toContainText("pnpm build:valuation-inputs:kr:top10");
  await expect(page.getByTestId("kr-top10-completion-command")).toContainText("pnpm load:valuation-warehouse:kr:top10");
  await expect(page.getByTestId("kr-top10-completion-command")).toContainText("run-priority-e2e --markets KR");
  await expect(page.getByTestId("e2e-completion-gate")).toContainText("E2E Completion Gate");
  await expect(page.getByTestId("e2e-completion-status")).toContainText("ready for warehouse load");
  await expect(page.getByTestId("e2e-completion-required-proofs")).toContainText("OpenDART financial facts");
  await expect(page.getByTestId("e2e-completion-commands")).toContainText("load-kr-valuation-warehouse --tickers 005930.KS");
  await expect(page.getByTestId("e2e-completion-deployment-command")).toContainText("DATA_BACKEND=postgres");
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByTestId("forecast-kr-source-readiness")).toContainText("Forecast KR source gate");
  await expect(page.getByTestId("forecast-kr-selected-status")).toContainText("partial source backed");
  await expect(page.getByTestId("forecast-kr-selected-backend")).toContainText("kr valuation input cache");
  await expect(page.getByTestId("forecast-kr-selected-years")).toContainText("2023, 2024");
  await expect(page.getByTestId("forecast-kr-universe-ready")).toContainText("10/10");
  await expect(page.getByTestId("forecast-kr-selected-ticker")).toContainText("005930.KS");
  await expect(page.getByTestId("forecast-kr-gap-ref-count")).toContainText("0");
  await expect(page.getByTestId("forecast-kr-selected-gap-ledger")).toContainText("complete");
  await expect(page.getByTestId("forecast-kr-selected-gap-ledger")).toContainText("No gap refs");
  await expect(page.getByTestId("forecast-kr-universe-source-doc")).toContainText("kr-valuation-cache-universe-summary");
  await page.getByRole("button", { name: "Performance" }).click();
  await expect(page.getByTestId("performance-kr-source-readiness")).toContainText("Performance KR source gate");
  await expect(page.getByTestId("performance-kr-selected-status")).toContainText("partial source backed");
  await expect(page.getByTestId("performance-kr-selected-backend")).toContainText("kr valuation input cache");
  await expect(page.getByTestId("performance-kr-universe-ready")).toContainText("10/10");
  await expect(page.getByTestId("performance-kr-selected-ticker")).toContainText("005930.KS");
  await expect(page.getByTestId("performance-kr-gap-ref-count")).toContainText("0");
  await expect(page.getByTestId("performance-kr-universe-source-doc")).toContainText("kr-valuation-cache-universe-summary");
  await page.getByRole("button", { name: "Historical" }).click();
  await expect(page.getByTestId("historical-high-low-strip")).toContainText("2024");
  await expect(page.getByTestId("historical-high-low-strip")).toContainText("81,000");
  await page.getByTestId("historical-readout-audit").click();
  await expect(page.getByTestId("data-audit-kr-diagnostics")).toContainText("KR Cache Diagnostics");
  await expect(page.getByTestId("data-audit-kr-market-gaps")).toContainText("source no rows before first trade");
  await expect(page.getByTestId("data-audit-kr-market-gaps")).toContainText("keep partial market history start");
  await expect(page.getByTestId("data-audit-kr-market-gaps")).toContainText("kr-cache:005930.KS:2022:market-gap");
  await page.getByTestId("data-audit-kr-market-gaps").getByRole("button", { name: /kr-cache:005930\.KS:2022/ }).click();
  await expect(page.getByTestId("raw-evidence-drawer")).toContainText("Raw Evidence");
  await expect(page.getByTestId("raw-evidence-status")).toContainText("found");
  await expect(page.getByTestId("raw-evidence-preview")).toContainText("partial_source_backed");
  await page.getByTestId("raw-evidence-close").click();
  await expect(page.getByTestId("data-audit-kr-market-gaps").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    new RegExp(`/api/data-audit/${marketGapFactId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)
  );
  await page.getByTestId("data-audit-kr-market-gaps").getByRole("button", { name: "Inspect" }).click();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("data_quality.kr_market_gap.source_no_rows_before_first_trade");
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open source doc" })).toHaveAttribute(
    "href",
    /\/api\/source-documents\/resolve\?source_document_id=kr-cache%3A005930\.KS%3A2022%3Amarket-gap%3Asource_no_rows_before_first_trade/
  );
  await page.getByTestId("selected-audit-trace").getByRole("button", { name: "Inspect source doc" }).click();
  await expect(page.getByTestId("raw-evidence-drawer")).toContainText("Raw Evidence");
  await expect(page.getByTestId("raw-evidence-status")).toContainText("found");
  await expect(page.getByTestId("raw-evidence-source-id")).toContainText("kr-cache:005930.KS:2022:market-gap");
  await expect(page.getByTestId("raw-evidence-preview")).toContainText("005930.KS");
  await expect(page.getByTestId("data-audit-kr-financial-gaps")).toContainText("source no data");
  await expect(page.getByTestId("data-audit-kr-financial-gaps")).toContainText("keep partial or add alternate source");
  await expect(page.getByTestId("data-audit-kr-financial-gaps")).toContainText("opendart:005930.KS:2022:status:013");
  await expect(page.getByTestId("data-audit-kr-financial-gaps").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    new RegExp(`/api/data-audit/${financialGapFactId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)
  );
  await page.getByRole("button", { name: "Historical" }).click();
  await page.getByTestId("kr-top10-partial-focus-402340.KS").click();
  await expect(page.getByTestId("quick-ticker-kr-priority-402340.KS")).toHaveClass(/active/);
  const focusedPartialRow = page.locator(".kr-top10-partial-row", { hasText: "402340.KS" });
  await expect(focusedPartialRow).toContainText("Open Data Audit");
  await focusedPartialRow.getByRole("button", { name: "Open Data Audit" }).first().click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("api_market_gap_ref");
});

test("KR valuation-map warehouse backend is treated as source-backed", async ({ page }) => {
  const derivedSourceDocumentId = "derived:kr:005930.KS:2024:valuation-input";
  const trace = {
    source: "kr_valuation_warehouse",
    source_document_id: derivedSourceDocumentId,
    source_type: "kr_valuation_warehouse_row",
    filing_id: "KR-WAREHOUSE-005930",
    period: "2024-12-31",
    unit: "KRW per share",
    currency: "KRW",
    method: "company_reported",
    formula: "metric = source-backed normalized fact loaded through kr_valuation warehouse",
    quality_status: "source_backed",
    quality_flags: []
  };
  const dividendTrace = {
    source: "opendart_dividends",
    source_document_id: "raw:opendart_dividends:005930.KS:2024",
    source_type: "opendart_dividends",
    filing_id: "00126380-2024-alotMatter",
    period: "FY2024",
    unit: "KRW/share",
    currency: "KRW",
    method: "OPENDART_ALOT_MATTER_DPS",
    formula: "cash_dividend_per_share from OpenDART alotMatter dividend disclosure",
    quality_status: "source_backed",
    quality_flags: ["source_backed_dividend"]
  };

  await page.route("**/api/v1/system/source-coverage**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        data_mode: "local_source_backed_warehouse",
        data_backend: "kr_valuation_warehouse",
        postgres: {
          reachable: false,
          error: "not_configured_local_warehouse"
        },
        requirements: {
          min_historical_years: 2,
          min_forecast_years: 5,
          consensus_forecast_required: false,
          core_required: ["security", "adjusted_earnings", "price_bars", "financial_metrics", "source_evidence"],
          consensus_forecast_optional: ["consensus_estimate_snapshots"]
        },
        summary: {
          tickers_expected: 1,
          core_ready: 1,
          consensus_forecast_ready: 0,
          missing_core: [],
          missing_consensus_forecast: ["005930.KS"],
          missing_by_requirement: {}
        },
        remediation: {
          status: "local_warehouse_ready",
          years: "2023:2025",
          next_actions: [],
          notes: ["Local warehouse proof ready; Neon/Postgres promotion required."]
        },
        tickers: [
          {
            ticker: "005930.KS",
            name: "Samsung Electronics",
            market: "KR",
            country: "South Korea",
            currency: "KRW",
            pattern: "mega_cap_semiconductor",
            status: "local_warehouse_ready",
            core_ready: true,
            consensus_forecast_ready: false,
            counts: {
              securities: 1,
              adjusted_earnings: 2,
              price_bars: 2,
              market_cap: 2,
              listed_shares: 2,
              financial_metrics: 3,
              source_documents: 2,
              raw_objects: 2,
              s3_periods: 2,
              consensus_estimates: 0
            },
            method_counts: {
              S3_MARKET_STANDARD_KR: 2,
              PYKRX_RAW_YEAR_END_CLOSE: 2,
              OPENDART_ALOT_MATTER_DPS: 2
            },
            available_metric_keys: ["adjusted_operating_eps", "price_close", "dividend_per_share"],
            missing_required: []
          }
        ]
      })
    });
  });

  await page.route("**/api/v1/companies/005930.KS/valuation-map**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            fiscal_year: 2023,
            period_end: "2023-12-31",
            metric: "6500",
            price: "73000",
            normal_multiple: "12",
            fair_multiple: "15",
            fair_value_price: "97500",
            forecast_flag: false,
            dividend: "1444",
            eps_yoy: "8.3",
            source_trace: {
              ...trace,
              period: "2023-12-31",
              filing_id: "KR-WAREHOUSE-005930-2023",
              dividend_source_trace: { ...dividendTrace, period: "FY2023", filing_id: "00126380-2023-alotMatter" }
            }
          },
          {
            fiscal_year: 2024,
            period_end: "2024-12-31",
            metric: "9000",
            price: "81000",
            normal_multiple: "12.5",
            fair_multiple: "15",
            fair_value_price: "135000",
            forecast_flag: false,
            dividend: "1444",
            eps_yoy: "38.5",
            source_trace: { ...trace, dividend_source_trace: dividendTrace }
          },
          {
            fiscal_year: 2025,
            period_end: "2025-12-31",
            metric: "9900",
            price: "99000",
            normal_multiple: "12.5",
            fair_multiple: "15",
            fair_value_price: "148500",
            forecast_flag: true,
            dividend: "1500",
            eps_yoy: "10",
            source_trace: {
              ...trace,
              period: "2025-12-31",
              method: "deterministic_forecast",
              formula: "metric = prior_year_metric * (1 + user_growth_rate)"
            }
          }
        ],
        meta: {
          ticker: "005930.KS",
          surface: "valuation_map",
          data_mode: "source_backed",
          data_backend: "kr_valuation_warehouse",
          quality_status: "source_backed",
          financial_numbers_allowed: true,
          kr_warehouse: {
            data_mode: "source_backed",
            data_backend: "kr_valuation_warehouse",
            financial_numbers_allowed: true,
            coverage_status: "warehouse_loaded",
            full_coverage_ready: true,
            valuation_ready: true,
            rejected_warehouse_rows: 0,
            warehouse_db_path: "data/warehouse/warehouse.duckdb",
            warehouse_views: {
              normalized_facts: "kr_normalized_facts",
              valuation_points: "kr_valuation_points"
            },
            cache_paths: ["storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json"],
            quality_flags: []
          },
          source_trace: trace,
          price_points: [
            {
              date: "2023-12-31",
              fiscal_year: 2023,
              close_price: "73000",
              currency: "KRW",
              frequency: "annual_source_warehouse",
              source_trace: trace
            },
            {
              date: "2024-12-31",
              fiscal_year: 2024,
              close_price: "81000",
              currency: "KRW",
              frequency: "annual_source_warehouse",
              source_trace: trace
            }
          ],
          forecast: {
            base_year: 2024,
            forecast_years: 1,
            source: "deterministic_user_input",
            source_trace: trace
          }
        }
      })
    });
  });
  await page.route("**/api/v1/companies/005930.KS/data-audit**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            fact_id: "005930.KS-2024-valuation.adjusted_operating_eps",
            fact_name: "valuation.adjusted_operating_eps",
            value: "9000",
            fiscal_year: 2024,
            method: "KR_SOURCE_BACKED_PRICE_EPS_JOIN",
            policy: "street_comparable",
            confidence: "0.85",
            quality_status: "source_backed",
            flags: ["source_backed_valuation_input"],
            formula: trace.formula,
            source_trace: trace
          },
          {
            fact_id: "005930.KS-2024-kr_warehouse.adjusted_operating_eps",
            fact_name: "kr_warehouse.adjusted_operating_eps",
            value: "9000",
            fiscal_year: 2024,
            method: "S3_MARKET_STANDARD_KR",
            policy: "kr_warehouse_normalized_fact",
            confidence: "0.85",
            quality_status: "source_backed",
            flags: ["source_trace_passed"],
            formula: "OpenDART reported EPS normalized as KR market-standard operating metric",
            source_trace: {
              ...trace,
              source_document_id: "raw:opendart:005930.KS:2024",
              source_type: "kr_warehouse_normalized_fact",
              method: "S3_MARKET_STANDARD_KR",
              formula: "OpenDART reported EPS normalized as KR market-standard operating metric",
              warehouse_view: "kr_normalized_facts",
              cache_path: "storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json"
            }
          },
          {
            fact_id: "005930.KS-2024-kr_warehouse.price_close",
            fact_name: "kr_warehouse.price_close",
            value: "81000",
            fiscal_year: 2024,
            method: "PYKRX_RAW_YEAR_END_CLOSE",
            policy: "kr_warehouse_normalized_fact",
            confidence: "0.85",
            quality_status: "source_backed",
            flags: ["source_trace_passed"],
            formula: "source-backed year-end close price from pykrx raw market data",
            source_trace: {
              ...trace,
              source_document_id: "raw:pykrx:005930.KS:2024",
              source_type: "kr_warehouse_normalized_fact",
              method: "PYKRX_RAW_YEAR_END_CLOSE",
              formula: "source-backed year-end close price from pykrx raw market data",
              warehouse_view: "kr_normalized_facts",
              cache_path: "storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json"
            }
          },
          {
            fact_id: "005930.KS-2024-kr_warehouse.dividend_per_share",
            fact_name: "kr_warehouse.dividend_per_share",
            value: "1444",
            fiscal_year: 2024,
            method: "OPENDART_ALOT_MATTER_DPS",
            policy: "kr_warehouse_normalized_fact",
            confidence: "0.85",
            quality_status: "source_backed",
            flags: ["source_trace_passed", "source_backed_dividend"],
            formula: "cash_dividend_per_share from OpenDART alotMatter dividend disclosure",
            source_trace: {
              ...dividendTrace,
              warehouse_view: "kr_normalized_facts",
              cache_path: "storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json"
            }
          }
        ],
        meta: {
          ticker: "005930.KS",
          total: 4,
          data_mode: "source_backed",
          data_backend: "kr_valuation_warehouse"
        }
      })
    });
  });
  await page.route("**/api/source-documents/resolve**", async (route) => {
    const url = new URL(route.request().url());
    const sourceDocumentId = url.searchParams.get("source_document_id") ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          source_document_id: sourceDocumentId,
          status: sourceDocumentId === derivedSourceDocumentId ? "found" : "logical_only",
          source: "kr_valuation_warehouse",
          content_hash: "warehouse-derived-source-hash",
          local_path: "storage/cache/kr-valuation-inputs/005930_KS-2020-2025-valuation-inputs.json",
          source_url: null,
          filing_url: null,
          content_type: "application/json",
          preview_available: sourceDocumentId === derivedSourceDocumentId,
          preview_text: sourceDocumentId === derivedSourceDocumentId
            ? JSON.stringify({
              ticker: "005930.KS",
              coverage_status: "complete",
              valuation_points: [{ fiscal_year: 2024, metric: "adjusted_operating_eps" }]
            }, null, 2)
            : null,
          resolver: "local_kr_warehouse_derived_valuation_input",
          metadata: {
            backing_source: "kr_valuation_input_cache"
          }
        }
      })
    });
  });

  await page.goto("/");

  await expect(page.locator(".status-pill")).toHaveText("API live");
  await expect(page.getByTestId("company-header-panel")).toContainText("source_backed");
  await expect(page.getByTestId("company-source-gate")).toContainText("Source-backed render path");
  await expect(page.getByTestId("historical-source-lock")).toHaveCount(0);
  await expect(page.getByTestId("historical-kr-source-contract-status")).toContainText("warehouse loaded");
  await expect(page.getByTestId("historical-kr-source-contract-backend")).toContainText("kr_valuation_warehouse");
  await expect(page.getByTestId("historical-kr-source-contract-years")).toContainText("2023, 2024");
  await expect(page.getByTestId("e2e-completion-gate")).toContainText("E2E Completion Gate");
  await expect(page.getByTestId("e2e-completion-status")).toContainText("complete");
  await expect(page.getByTestId("e2e-completion-local-status")).toContainText("warehouse rows are loaded");
  await page.getByTestId("year-column-2024").click();
  await expect(page.getByTestId("historical-dividend-provenance")).toContainText("Dividend provenance");
  await expect(page.getByTestId("historical-dividend-provenance-source")).toContainText("opendart_dividends");
  await expect(page.getByTestId("historical-dividend-provenance-method")).toContainText("OPENDART_ALOT_MATTER_DPS");
  await expect(page.getByTestId("historical-dividend-provenance-flag")).toContainText("source_backed_dividend");
  await expect(page.getByTestId("kr-cache-coverage")).toContainText("kr_valuation_warehouse");
  await expect(page.getByTestId("kr-cache-coverage")).toContainText("KR valuation warehouse");
  await expect(page.getByTestId("kr-warehouse-proof")).toContainText("data/warehouse/warehouse.duckdb");
  await expect(page.getByTestId("kr-warehouse-proof")).toContainText("kr_normalized_facts / kr_valuation_points");
  await expect(page.getByTestId("kr-warehouse-proof")).toContainText("Rejected rows");
  await expect(page.getByTestId("kr-warehouse-load-command")).toContainText("pnpm load:valuation-warehouse:kr:005930");
  await expect(page.getByTestId("kr-top10-production-gate")).toContainText("Local warehouse proof ready; Neon/Postgres promotion required");
  await expect(page.getByTestId("kr-top10-completion-rows")).toContainText("local warehouse only");
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByTestId("forecast-kr-selected-status")).toContainText("warehouse loaded");
  await expect(page.getByTestId("forecast-kr-selected-backend")).toContainText("kr valuation warehouse");
  await page.getByRole("button", { name: "Performance" }).click();
  await expect(page.getByTestId("performance-kr-selected-backend")).toContainText("kr valuation warehouse");
  await page.getByRole("button", { name: "Financials" }).click();
  await expect(page.getByRole("heading", { name: "Financials" })).toBeVisible();
  await expect(page.getByTestId("financials-kr-selected-status")).toContainText("warehouse loaded");
  await expect(page.getByTestId("financials-kr-selected-backend")).toContainText("kr valuation warehouse");
  await expect(page.getByTestId("financials-p1-contract")).toContainText("No source_trace, no financial statement number");
  await page.getByRole("button", { name: "Historical" }).click();
  await page.getByTestId("year-column-2024").focus();
  await page.keyboard.press("Shift+Enter");
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("audit-family-valuation_derived")).toHaveClass(/active/);
  await expect(page.getByTestId("selected-audit-trace")).toContainText("valuation.adjusted_operating_eps");
  await page.getByRole("button", { name: "Historical" }).click();
  await page.getByTestId("historical-readout-audit").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("audit-family-ledger")).toBeVisible();
  await expect(page.getByTestId("audit-family-warehouse_metric")).toContainText("Warehouse EPS / Metric");
  await expect(page.getByTestId("audit-family-warehouse_price")).toContainText("Warehouse Price");
  await expect(page.getByTestId("audit-family-valuation_derived")).toContainText("Valuation Derived");
  await expect(page.getByTestId("selected-audit-trace")).toContainText(derivedSourceDocumentId);
  await page.getByTestId("audit-family-warehouse_metric").click();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("kr_warehouse.adjusted_operating_eps");
  await expect(page.getByTestId("selected-audit-trace")).toContainText("kr_normalized_facts");
  await page.getByTestId("audit-family-warehouse_price").click();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("kr_warehouse.price_close");
  await page.getByTestId("audit-family-warehouse_metric").click();
  await page.getByTestId("data-audit-fact-005930.KS-2024-kr_warehouse.dividend_per_share").click();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("kr_warehouse.dividend_per_share");
  await expect(page.getByTestId("selected-audit-trace")).toContainText("OPENDART_ALOT_MATTER_DPS");
  await expect(page.getByTestId("selected-audit-trace")).toContainText("opendart_dividends");
  await page.getByTestId("audit-family-valuation_derived").click();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("valuation.adjusted_operating_eps");
  await page.getByTestId("selected-audit-trace").getByRole("button", { name: "Inspect source doc" }).click();
  await expect(page.getByTestId("raw-evidence-drawer")).toContainText("Raw Evidence");
  await expect(page.getByTestId("raw-evidence-status")).toContainText("found");
  await expect(page.getByTestId("raw-evidence-source-id")).toContainText(derivedSourceDocumentId);
  await expect(page.getByTestId("raw-evidence-preview")).toContainText("adjusted_operating_eps");
});

test("priority universe rail exposes partial market-cap rank coverage", async ({ page }) => {
  await page.route("**/api/v1/system/priority-universe?market=ALL", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        universe_id: "global-top-market-cap-source-backed-v1",
        label: "KR/US/JP source-backed top market-cap universes",
        markets: ["KR"],
        data_mode: "source_backed",
        note: "Source-backed ranks are partial until each market has 10 current market-cap rows.",
        universes: [
          {
            universe_id: "kr-top-market-cap-source-backed-v1",
            label: "KR source-backed top market-cap universe",
            market: "KR",
            currency: "KRW",
            data_mode: "source_backed",
            rank_policy: "source_backed_latest_market_cap",
            rank_coverage_status: "partial_top_market_cap_rank",
            rank_count: 3,
            rank_limit: 10,
            missing_rank_slots: 7,
            note: "Three source-backed market-cap rows loaded.",
            source_trace: {
              source_document_id: "postgres-price-bars-latest-market-cap-kr",
              source_type: "source_backed_market_cap_rank",
              filing_id: "POSTGRES-KR-LATEST-MARKET-CAP",
              period: "2026-06-26",
              unit: "market_cap",
              currency: "KRW",
              method: "source_backed_latest_market_cap_rank",
              formula: "Select latest market_cap evidence per ticker, sort descending, keep top rows.",
              quality_status: "partial_source_backed_market_cap_rank",
              quality_flags: ["partial_market_cap_rank", "missing_rank_slots"]
            },
            tickers: [
              ["005930.KS", "Samsung Electronics", "500000000000000"],
              ["000660.KS", "SK hynix", "300000000000000"],
              ["005380.KS", "Hyundai Motor", "120000000000000"]
            ].map(([ticker, name, marketCap], index) => ({
              ticker,
              name,
              market: "KR",
              currency: "KRW",
              market_cap: marketCap,
              market_cap_rank: index + 1,
              market_cap_rank_input_date: "2026-06-26",
              coverage_priority_order: index + 1,
              rank_policy: "source_backed_latest_market_cap",
              source_trace: {
                source_document_id: `marcap-2026-06-26-${ticker}`,
                source_type: "marcap",
                filing_id: `marcap-2026-06-26-${ticker}`,
                period: "2026-06-26",
                unit: "market_cap",
                currency: "KRW",
                method: "MARCAP_DAILY_CLOSE",
                formula: "market_cap_rank = descending latest source-backed market_cap from price_bars",
                quality_status: "source_backed_market_data"
              }
            }))
          }
        ]
      })
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Underwrite" }).click();
  await page.getByRole("button", { name: "Historical" }).click();

  const card = page.getByTestId("priority-universe-contract");
  await expect(card).toContainText("KR/US/JP source-backed top market-cap universes");
  await expect(page.getByTestId("priority-universe-rank-label")).toContainText("Partial source-backed market cap rank");
  await expect(page.getByTestId("priority-universe-rank-coverage")).toContainText("Rank coverage: partial top market cap rank (3/10)");
  await expect(page.getByTestId("priority-universe-rank-missing")).toContainText("Missing rank slots 7");
  await expect(page.getByTestId("priority-universe-readiness")).toContainText("7 rank slots pending");
  await expect(page.getByTestId("priority-universe-readiness")).toContainText("30% market-cap rank evidence");
  await expect(page.getByTestId("priority-universe-row-005930.KS")).toContainText("1");
  await expect(page.getByTestId("priority-universe-rank-policy-005930.KS")).toContainText("KR / source backed latest market cap");
});

test("current source gate shows the selected ticker remediation command", async ({ page }) => {
  await page.route("**/api/v1/companies/005930.KS/valuation-map**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [],
        meta: {
          ticker: "005930.KS",
          surface: "valuation_map",
          data_mode: "source_backed_required",
          data_backend: "postgres_required",
          quality_status: "missing_source_backed_data",
          financial_numbers_allowed: false,
          source_note: "KR priority coverage requires OpenDART/pykrx/marcap source-backed rows.",
          source_trace: {
            source_document_id: "nexus-kr-top-market-cap-priority-universe-v2",
            source_type: "product_priority_universe_contract",
            filing_id: "NEXUS-KR-TOP-MARKET-CAP-PRIORITY-V2",
            period: "initial_coverage",
            unit: "ticker_list",
            currency: "N/A",
            method: "source_backed_required_gate",
            formula: "fixture financial values are blocked until source-backed rows are loaded",
            quality_status: "missing_source_backed_data",
            quality_flags: ["fixture_fallback_blocked_for_kr_priority"],
            financial_numbers_allowed: false
          }
        }
      })
    });
  });
  await page.route("**/api/v1/system/source-coverage**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "partial",
        data_mode: "source_backed_required",
        postgres: { reachable: true, error: null },
        requirements: {
          min_historical_years: 3,
          min_forecast_years: 5,
          consensus_forecast_required: true,
          core_required: ["security", "adjusted_earnings", "price_bars", "financial_metrics", "source_evidence"],
          consensus_forecast_optional: ["consensus_estimate_snapshots"]
        },
        summary: {
          tickers_expected: 1,
          core_ready: 0,
          consensus_forecast_ready: 0,
          missing_core: ["005930.KS"],
          missing_consensus_forecast: ["005930.KS"],
          missing_by_requirement: {
            financial_metrics: ["005930.KS"],
            consensus_forecast: ["005930.KS"]
          }
        },
        remediation: {
          status: "needs_source_data",
          years: "2020:2025",
          forecast_csv_preflight: {
            path: "storage/imports/consensus_005930.csv",
            exists: true,
            status: "template_pending",
            tickers: ["005930.KS"],
            required_periods: 5,
            rows: 5,
            candidate_rows: 5,
            ready_rows: 0,
            covered_periods: 0,
            missing_periods: [
              { ticker: "005930.KS", fiscal_year: 2026, estimate_cases_allowed: ["median", "current"] },
              { ticker: "005930.KS", fiscal_year: 2027, estimate_cases_allowed: ["median", "current"] },
              { ticker: "005930.KS", fiscal_year: 2028, estimate_cases_allowed: ["median", "current"] },
              { ticker: "005930.KS", fiscal_year: 2029, estimate_cases_allowed: ["median", "current"] },
              { ticker: "005930.KS", fiscal_year: 2030, estimate_cases_allowed: ["median", "current"] }
            ],
            missing_value_rows: 5,
            missing_trace_rows: 5,
            missing_manual_notes_rows: 0,
            invalid_value_rows: 0,
            invalid_currency_rows: 0,
            blocked_evidence_rows: 0,
            manual_assumption_ready_rows: 0,
            external_consensus_ready_rows: 0,
            assumption_types: {
              manual_assumption: 0,
              external_consensus: 0
            },
            import_ready_candidate: false,
            strict_validator:
              "python -m services.ingestion_worker.cli validate-consensus-csv --path storage/imports/consensus_005930.csv --tickers 005930.KS --cases median,current --case-mode any --strict"
          },
          next_actions: [
            {
              id: "build_kr_valuation_inputs",
              priority: 20,
              requirements: ["financial_metrics"],
              tickers: ["005930.KS"],
              description: "Build source-traced KR valuation inputs before production display.",
              cli_commands: [
                "python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers 005930.KS --years 2020:2025 --strict"
              ],
              github_actions: {
                workflow: "ingestion-worker.yml",
                command: "build_kr_valuation_inputs",
                coverage_tickers: "005930.KS"
              }
            }
          ],
          notes: ["run actions in priority order"]
        },
        tickers: [
          {
            ticker: "005930.KS",
            name: "Samsung Electronics",
            market: "KR",
            country: "KR",
            currency: "KRW",
            pattern: "kr_top_market_cap",
            status: "partial",
            core_ready: false,
            consensus_forecast_ready: false,
            counts: {
              security: 1,
              adjusted_years: 2,
              price_years: 2,
              financial_metric_years: 0,
              consensus_valuation_years: 0,
              consensus_valuation_snapshots: 0
            },
            method_counts: { s1: 0, s2: 0, s4: 2 },
            available_metric_keys: ["gaap_diluted_eps"],
            missing_required: ["financial_metrics", "consensus_forecast"]
          }
        ]
      })
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Underwrite" }).click();
  await page.getByRole("button", { name: "Historical" }).click();

  await expect(page.getByTestId("current-source-gate")).toContainText("005930.KS source gate blocked");
  await expect(page.getByTestId("current-source-gate-readiness")).toContainText("core pending / forecast pending");
  await expect(page.getByTestId("current-source-gate-missing")).toContainText(
    "Missing financial metrics, consensus forecast"
  );
  await expect(page.getByTestId("current-source-gate-command")).toContainText("build-kr-valuation-inputs");
  await expect(page.getByTestId("e2e-completion-gate")).toContainText("E2E Completion Gate");
  await expect(page.getByTestId("e2e-completion-status")).toContainText("ready for valuation cache build");
  await expect(page.getByTestId("e2e-completion-local-status")).toContainText("Build valuation inputs");
  await expect(page.getByTestId("e2e-completion-commands")).toContainText("build-kr-valuation-inputs --tickers 005930.KS");
  await expect(page.getByTestId("e2e-completion-commands")).toContainText("load-kr-valuation-warehouse --tickers 005930.KS");
  await expect(page.getByTestId("e2e-completion-commands")).toContainText("test_kr_priority_valuation_map_uses_warehouse_before_cache");
  await expect(page.getByTestId("e2e-completion-deployment-command")).toContainText("source-coverage --market KR --tickers 005930.KS");
  await expect(page.getByTestId("e2e-completion-deployment-command")).toContainText("DATA_BACKEND=postgres");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("Forecast CSV template pending");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("storage/imports/consensus_005930.csv");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("Ready 0/5 rows");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("trace missing 5");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("manual notes missing 0");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("External consensus 0");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("manual assumption 0");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("invalid values 0");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("blocked evidence 0");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("Missing FY 005930.KS FY2026");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("005930.KS FY2030");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("validate-consensus-csv");
});

test("current source gate shows local forecast overlay ready while production DB is pending", async ({ page }) => {
  const forecastTrace = {
    source: "manual_forecast_assumption",
    source_type: "manual_forecast_assumption",
    source_document_id: "MANUAL_FORECAST_ASSUMPTION_005930.KS_2026_2030_LOCAL_FY2026_MEDIAN",
    upstream_source_document_id: "manual-forecast-assumption:005930.KS:2026:2030:local",
    filing_id: "MANUAL_FORECAST_ASSUMPTION_005930.KS_2026_2030_LOCAL_FY2026_MEDIAN",
    period: "FY2026E",
    available_at: "2026-07-02T00:00:00+00:00",
    unit: "per_share",
    currency: "KRW",
    method: "explicit_manual_assumption",
    formula: "explicit user forecast assumption imported from a source-traced local CSV; no LLM-generated numbers",
    quality_status: "source_backed_manual_forecast_assumption",
    assumption_type: "manual_assumption",
    llm_generated_numbers: false,
    ai_role: "commentary_only"
  };
  await page.route("**/api/v1/companies/005930.KS/valuation-map**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [],
        meta: {
          ticker: "005930.KS",
          data_mode: "source_backed",
          data_backend: "kr_valuation_warehouse",
          forecast: {
            years: 5,
            mode: "estimates",
            case: "median",
            consensus: {
              quality_status: "source_backed_manual_forecast_assumption"
            }
          }
        }
      })
    });
  });
  await page.route("**/api/v1/companies/005930.KS/data-audit**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            fact_id: "005930.KS-2026-forecast.metric",
            fact_name: "forecast.metric",
            value: "7358.69",
            fiscal_year: 2026,
            method: "consensus_snapshot",
            policy: "forecast",
            confidence: null,
            quality_status: "source_backed_manual_forecast_assumption",
            flags: [],
            formula: forecastTrace.formula,
            source_trace: forecastTrace
          }
        ],
        meta: {
          ticker: "005930.KS",
          total: 1,
          data_mode: "source_backed"
        }
      })
    });
  });
  await page.route("**/api/v1/system/source-coverage**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        data_mode: "local_source_backed_warehouse",
        data_backend: "kr_valuation_warehouse",
        postgres: { reachable: false, error: "not_configured_local_warehouse" },
        local_overlays: {
          forecast_csv: "enabled",
          production_db_pending: true
        },
        requirements: {
          min_historical_years: 3,
          min_forecast_years: 5,
          consensus_forecast_required: true,
          core_required: ["security", "adjusted_earnings", "price_bars", "financial_metrics", "source_evidence"],
          consensus_forecast_optional: ["consensus_estimate_snapshots"]
        },
        summary: {
          tickers_expected: 1,
          core_ready: 1,
          consensus_forecast_ready: 1,
          missing_core: [],
          missing_consensus_forecast: [],
          missing_by_requirement: {}
        },
        remediation: {
          status: "ready",
          years: "2020:2025",
          forecast_csv_preflight: {
            path: "storage/imports/consensus_005930.csv",
            exists: true,
            status: "candidate_ready",
            tickers: ["005930.KS"],
            required_periods: 5,
            rows: 5,
            candidate_rows: 5,
            ready_rows: 5,
            covered_periods: 5,
            missing_periods: [],
            missing_value_rows: 0,
            missing_trace_rows: 0,
            missing_manual_notes_rows: 0,
            invalid_value_rows: 0,
            invalid_currency_rows: 0,
            blocked_evidence_rows: 0,
            manual_assumption_ready_rows: 5,
            external_consensus_ready_rows: 0,
            assumption_types: {
              manual_assumption: 5,
              external_consensus: 0
            },
            import_ready_candidate: true,
            strict_validator:
              "python -m services.ingestion_worker.cli validate-consensus-csv --path storage/imports/consensus_005930.csv --tickers 005930.KS --cases median,current --case-mode any --strict"
          },
          next_actions: [],
          notes: ["local CSV overlay ready; protected deployment still requires Postgres import"]
        },
        tickers: [
          {
            ticker: "005930.KS",
            name: "Samsung Electronics",
            market: "KR",
            country: "KR",
            currency: "KRW",
            pattern: "kr_top_market_cap",
            status: "ready",
            core_ready: true,
            consensus_forecast_ready: true,
            local_consensus_overlay_ready: true,
            local_consensus_overlay_source: "local_consensus_csv",
            counts: {
              security: 1,
              adjusted_years: 6,
              price_years: 6,
              market_cap_years: 6,
              listed_shares_years: 6,
              financial_metric_years: 6,
              financial_metric_keys: 7,
              source_documents: 50,
              raw_objects: 50,
              consensus_forecast_years: 5,
              consensus_valuation_years: 5,
              consensus_snapshots: 5,
              consensus_valuation_snapshots: 5
            },
            method_counts: { s1: 0, s2: 0, s4: 6 },
            available_metric_keys: ["adjusted_operating_eps"],
            missing_required: []
          }
        ]
      })
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Underwrite" }).click();
  await page.getByRole("button", { name: "Historical" }).click();

  await expect(page.getByTestId("current-source-gate")).toContainText("005930.KS source-ready");
  await expect(page.getByTestId("current-source-gate-readiness")).toContainText("core ready / forecast ready");
  await expect(page.getByTestId("current-source-gate-forecast-overlay")).toContainText(
    "Local CSV forecast overlay ready / production DB pending"
  );
  await expect(page.getByTestId("current-source-gate-missing")).toContainText("No required source gaps");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("Forecast CSV candidate ready");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("Ready 5/5 rows");
  await expect(page.getByTestId("forecast-csv-preflight")).toContainText("manual assumption 5");
  await page.getByRole("button", { name: "Data Audit", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace")).toContainText("forecast.metric");
  await expect(page.getByTestId("selected-audit-trace")).toContainText(
    "MANUAL_FORECAST_ASSUMPTION_005930.KS_2026_2030_LOCAL_FY2026_MEDIAN"
  );
  await expect(page.getByTestId("selected-audit-trace")).toContainText("source_backed_manual_forecast_assumption");
  await expect(page.getByTestId("selected-audit-trace")).toContainText("FY2026E");
});

test("valuation terminal renders forecast controls and audit waterfall", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await expect(page.locator(".brand-mark img").first()).toHaveAttribute("src", "/valuetrace-mark.svg");
  const iconHrefs = await page.locator("link[rel='icon']").evaluateAll((links) =>
    links.map((link) => link.getAttribute("href") ?? "")
  );
  expect(iconHrefs.some((href) => href.includes("icon.svg") || href.includes("favicon.ico"))).toBeTruthy();
  await expect(page.locator("link[rel='apple-touch-icon']")).toHaveAttribute("href", "/valuetrace-mark.png");
  await expect(page.locator("meta[property='og:image']")).toHaveAttribute("content", /valuetrace-og\.png/);
  await expect(page.locator(".status-pill")).toHaveText(/source required|API live/);
  await expect(page.locator(".terminal-wordmark strong")).toHaveText("LUXON");
  await expect(page.getByTestId("global-search-trigger")).toContainText("/ Search securities, portfolios, screens, source traces");
  await expect(page.getByTestId("topbar-deployment-status")).toContainText("Local private");
  await expect(page.getByTestId("topbar-deployment-status")).toContainText("Source-traced");
  await expect(page.locator(".side-nav")).toHaveAttribute("aria-label", "LUXON workspace navigation");
  await expect(page.getByRole("button", { name: "LUXON rail H" })).toHaveText("H");
  await expect(page.getByRole("button", { name: "LUXON rail H" })).toHaveAttribute("title", "Home");
  await expect(page.getByRole("button", { name: "LUXON rail T" })).toHaveText("T");
  await expect(page.getByRole("button", { name: "LUXON rail T" })).toHaveAttribute("title", "Terminal");
  await expect(page.getByRole("button", { name: "LUXON rail S" })).toHaveText("S");
  await expect(page.getByRole("button", { name: "LUXON rail S" })).toHaveAttribute("title", "Screens");
  await expect(page.getByRole("button", { name: "LUXON rail P" })).toHaveText("P");
  await expect(page.getByRole("button", { name: "LUXON rail P" })).toHaveAttribute("title", "Portfolio");
  await expect(page.getByRole("button", { name: "LUXON rail A" })).toHaveText("A");
  await expect(page.getByRole("button", { name: "LUXON rail A" })).toHaveAttribute("title", "Audit");
  await expect(page.getByRole("button", { name: "LUXON rail T" })).toHaveClass(/active/);
  await expect(page.getByTestId("company-header-panel")).toContainText("Source-traced data");
  await expect(page.getByTestId("company-header-panel")).toContainText("source_backed");
  await expect(page.getByTestId("company-header-panel")).toContainText("Add to Portfolio");
  const sourceGateText = await page.getByTestId("company-source-gate").innerText();
  expect(sourceGateText).toMatch(/KR E2E source gate|Source-backed render path/);
  await expect(page.getByTestId("company-source-gate")).toContainText("OpenDART + pykrx + marcap");
  await expect(page.getByTestId("company-source-gate")).toContainText("no source_trace, no number");
  if (sourceGateText.includes("KR E2E source gate")) {
    await expect(page.getByTestId("historical-source-lock")).toContainText("Chart waits for source-traced rows");
  } else {
    await expect(page.getByTestId("historical-source-lock")).toHaveCount(0);
  }
  await expect(page.getByRole("navigation", { name: "Company workspace tabs" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Historical" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Add to Portfolio" }).click();
  await expect(page.getByRole("heading", { name: "Portfolio" })).toBeVisible();
  await page.getByRole("button", { name: "LUXON rail S" }).click();
  await expect(page.getByRole("heading", { name: "Screener" })).toBeVisible();
  await page.getByRole("button", { name: "LUXON rail P" }).click();
  await expect(page.getByRole("heading", { name: "Portfolio" })).toBeVisible();
  await page.getByRole("button", { name: "LUXON rail A" }).click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await page.getByRole("button", { name: "LUXON rail H" }).click();
  await expect(page.getByRole("heading", { name: "Company Terminal" })).toBeVisible();
  await page.getByRole("button", { name: "LUXON rail T" }).click();
  await expect(page.getByRole("heading", { name: "Historical Valuation Map" })).toBeVisible();
  await page.getByRole("button", { name: "Open product tour" }).click();
  let productTourDialog = page.getByRole("dialog", { name: "Command Workspace" });
  await expect(productTourDialog).toBeVisible();
  await expect(productTourDialog).toContainText("visual dashboard");
  await expect(page.getByText("1 of 5")).toBeVisible();
  await productTourDialog.getByRole("button", { name: "Next", exact: true }).click();
  productTourDialog = page.getByRole("dialog", { name: "Historical Valuation Map" });
  await expect(productTourDialog).toBeVisible();
  await expect(page.getByText("2 of 5")).toBeVisible();
  await productTourDialog.getByRole("button", { name: "Close product tour" }).click();
  await expect(page.getByText("Source readiness", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ask, forecast, then verify the source." })).toBeVisible();
  await expect(page.getByTestId("workspace-map")).toContainText("Forecast Lab");
  await expect(page.getByTestId("workspace-map")).toContainText("Data Audit");
  await expect(page.getByTestId("quick-tickers-kr-priority")).toContainText("KR priority universe");
  await expect(page.getByTestId("quick-tickers-kr-priority")).toContainText("10 tickers");
  await expect(page.getByTestId("quick-tickers-kr-priority")).toContainText("source_trace required");
  await expect(page.getByTestId("quick-tickers-us-priority")).toContainText("US priority universe");
  await expect(page.getByTestId("quick-tickers-jp-priority")).toContainText("JP priority universe");
  await expect(page.getByTestId("quick-ticker-kr-priority-005930.KS")).toBeVisible();
  await expect(page.getByTestId("quick-ticker-kr-priority-005930.KS")).toHaveClass(/active/);
  await expect(page.getByTestId("quick-ticker-kr-priority-009155.KS")).toBeVisible();
  await expect(page.getByTestId("quick-ticker-us-priority-AAPL")).toBeVisible();
  await expect(page.getByTestId("quick-ticker-jp-priority-7203.T")).toBeVisible();
  await page.getByTestId("quick-ticker-kr-priority-000660.KS").click();
  await expect(page.getByTestId("quick-ticker-kr-priority-000660.KS")).toHaveClass(/active/);
  await expect(page.locator(".status-pill")).toHaveText("source required");
  await page.getByTestId("quick-ticker-us-priority-AAPL").click();
  await expect(page.getByTestId("quick-ticker-us-priority-AAPL")).toHaveClass(/active/);
  await expect(page.locator(".status-pill")).toHaveText(/API live|fixture fallback/);
  await expect(page.getByTestId("visualization-coverage-card")).toContainText("Visualization coverage");
  await expect(page.getByTestId("visualization-coverage-card")).toContainText("core dashboard layer");
  await expect(page.getByTestId("visual-coverage-historical_map")).toContainText("Historical map");
  await expect(page.getByTestId("visual-coverage-forecast_fan")).toContainText("Forecast fan");
  await expect(page.getByTestId("visual-coverage-data_audit")).toContainText("Data Audit");
  await expect(page.getByTestId("visual-coverage-trade_overlays")).toContainText("Trade overlays");
  await page.getByRole("button", { name: "Underwrite" }).click();
  await page.getByRole("button", { name: "Historical" }).click();
  const historicalHeading = page.getByRole("heading", { name: "Historical Valuation Map" });
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(historicalHeading).toBeVisible();
  await expect(page.getByText("Adjusted EPS Audit")).toBeVisible();
  await expect(page.getByText("Source readiness", { exact: true })).toBeVisible();
  await expect(page.getByText("MVP Source Coverage", { exact: true })).toBeVisible();
  await expect(page.getByText("Core ready", { exact: true })).toBeVisible();
  await expect(page.getByText("Base forecast EPS", { exact: true })).toBeVisible();
  await expect(page.getByTestId("priority-universe-contract")).toContainText("KR/US/JP top-market-cap priority universes");
  await expect(page.getByTestId("priority-universe-contract")).toContainText("Not live market cap rank");
  await expect(page.getByTestId("priority-universe-source-doc")).toContainText("nexus-global-top-market-cap-priority-universe-v1");
  await expect(page.getByTestId("priority-universe-count")).toContainText("30 tickers");
  await expect(page.getByTestId("priority-universe-rank-coverage")).toContainText("Rank coverage: coverage contract only (0/30)");
  await expect(page.getByTestId("priority-universe-rank-missing")).toContainText("Missing rank slots 30");
  await expect(page.getByTestId("priority-universe-row-005930.KS")).toContainText("1");
  await expect(page.getByTestId("priority-universe-rank-policy-005930.KS")).toContainText("KR / not a live market cap rank");
  await expect(page.getByTestId("priority-universe-row-009155.KS")).toContainText("10");
  await expect(page.getByTestId("priority-universe-row-009155.KS")).toContainText("009155.KS");
  await expect(page.getByTestId("priority-universe-row-NVDA")).toContainText("1");
  await expect(page.getByTestId("priority-universe-row-6981.T")).toContainText("10");
  await expect(page.getByTestId("source-coverage-gaps").getByText("financial metrics")).toBeVisible();
  await expect(page.getByTestId("source-coverage-gaps").getByText("source evidence")).toBeVisible();
  await expect(page.getByTestId("source-coverage-actions").getByText("Next ingestion actions")).toBeVisible();
  await expect(page.getByTestId("source-coverage-actions").getByText("run priority e2e", { exact: true })).toBeVisible();
  await expect(page.getByTestId("source-coverage-row-005930.KS").getByText("005930.KS", { exact: true })).toBeVisible();
  await expect(page.getByTestId("source-coverage-base-forecast-005930.KS")).toHaveText("Base EPS 0/5Y");
  await expect(page.getByTestId("source-coverage-row-000660.KS").getByText("kr top market cap")).toBeVisible();
  await page.getByRole("button", { name: "Summary" }).click();
  await expect(page.getByRole("heading", { name: "Company Terminal" })).toBeVisible();
  await expect(page.getByTestId("summary-valuation-preview")).toContainText("Valuation map preview");
  await expect(page.getByTestId("summary-preview-price-line")).toBeVisible();
  await expect(page.getByTestId("summary-preview-metric-area")).toBeVisible();
  await expect(page.getByTestId("summary-preview-normal-line")).toBeVisible();
  await expect(page.getByTestId("summary-preview-fair-line")).toBeVisible();
  await expect(page.getByTestId("summary-preview-dividend-line")).toHaveAttribute("points", /\d+\.\d+,\d+\.\d+/);
  await expect(page.getByTestId("summary-preview-forecast-region")).toBeVisible();
  await expect(page.getByTestId("summary-preview-audit-fact-metric")).toBeVisible();
  await expect(page.getByTestId("summary-preview-audit-fact-price")).toBeVisible();
  await expect(page.getByTestId("summary-preview-audit-fact-fair_value_price")).toBeVisible();
  await expect(page.getByTestId("summary-preview-audit-fact-normal_multiple")).toBeVisible();
  await expect(page.getByTestId("summary-preview-audit-fact-dividend")).toBeVisible();
  await page.getByTestId("summary-preview-marker-2024").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.metric")).toBeVisible();
  await expect(page.getByTestId("selected-audit-storage-contract")).toContainText("Storage gate");
  await expect(page.getByTestId("selected-audit-storage-status")).toContainText(/display allowed|source_trace incomplete/);
  await expect(page.getByTestId("selected-audit-storage-field-source")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-source_document_id")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-filing")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-period")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-unit")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-currency")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-method")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-field-formula")).toContainText("ready");
  await expect(page.getByTestId("selected-audit-storage-missing")).toContainText(/no missing storage fields|missing/);
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.metric/
  );
  await page.getByRole("button", { name: "Summary" }).click();
  await expect(page.getByRole("heading", { name: "Company Terminal" })).toBeVisible();
  await page.getByTestId("summary-preview-audit-fact-price").click();
  await page.getByTestId("summary-preview-marker-2024").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.price/
  );
  await page.getByRole("button", { name: "Summary" }).click();
  await expect(page.getByRole("heading", { name: "Company Terminal" })).toBeVisible();
  await page.getByTestId("summary-audit-cell-current_price").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("snapshot.current_price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-snapshot\.current_price/
  );
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(historicalHeading).toBeVisible();
  await expect(page.getByRole("button", { name: "Forecasting" })).toBeVisible();
  const historicalControlsBand = page.getByTestId("historical-controls-band");
  await expect(historicalControlsBand).toContainText("Price Correlated With");
  await expect(historicalControlsBand).toContainText("Chart settings");
  await expect(historicalControlsBand).toContainText("Choose dates");
  const metricSelector = page.locator(".control-strip select").first();
  await expect(metricSelector.locator("option[value='adjusted_operating']")).toHaveJSProperty("disabled", false);
  await expect(metricSelector.locator("option[value='diluted_eps']")).toHaveJSProperty("disabled", false);
  await expect(metricSelector.locator("option[value='sales_share']")).toHaveJSProperty("disabled", false);
  await expect(metricSelector.locator("option[value='smart_metric']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='basic_eps']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='operating_cash_flow_share']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='fcf_share']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='ebitda_share']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='ebit_share']")).toHaveJSProperty("disabled", true);
  await expect(metricSelector.locator("option[value='ffo_affo']")).toHaveJSProperty("disabled", true);
  await page.locator(".metric-selector-trigger").click();
  await expect(page.getByTestId("metric-selector-source-guard")).toContainText("No source_trace");
  await expect(page.getByTestId("metric-selector-coverage")).toContainText("Available");
  await expect(page.getByTestId("metric-selector-available-count")).toHaveText("3");
  await expect(page.getByTestId("metric-selector-locked-count")).toHaveText("7");
  await expect(page.getByTestId("metric-option-smart_metric")).toBeDisabled();
  await expect(page.getByTestId("metric-option-reason-smart_metric")).toContainText("source-backed sector rules required");
  await expect(page.getByTestId("metric-option-source-adjusted_operating")).toContainText("adjusted earnings engine");
  await expect(page.getByTestId("metric-option-source-diluted_eps")).toContainText("GAAP filing metric");
  await expect(page.getByTestId("metric-option-fcf_share")).toBeDisabled();
  await expect(page.getByTestId("metric-option-reason-fcf_share")).toContainText("source-backed FCF/share required");
  await expect(page.getByTestId("metric-option-source-fcf_share")).toContainText("waiting for source-backed row");
  await expect(page.getByTestId("metric-option-sales_share")).toBeEnabled();
  await expect(page.getByTestId("metric-option-source-sales_share")).toContainText("revenue/share source trace");
  await page.locator(".metric-selector-trigger").click();
  await metricSelector.selectOption("adjusted_operating");
  await page.getByTestId("chart-settings-band-trigger").click();
  await expect(page.getByTestId("chart-settings-band-trigger")).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByLabel("Normal P/E window")).toBeVisible();
  await page.getByLabel("Normal P/E window").selectOption("3");
  await expect(page.getByTestId("chart-settings-drawer").getByText("Normal window", { exact: true })).toBeVisible();
  await expect(page.getByTestId("chart-settings-drawer").getByText("3FY", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Historical range")).toBeVisible();
  await page.getByLabel("Historical range").selectOption("3");
  await expect(page.getByLabel("Range start year")).toHaveValue("2022");
  await expect(page.getByLabel("Range end year")).toHaveValue("2024");
  await page.getByLabel("Historical range").selectOption("max");
  await expect(page.getByLabel("Range start year")).toHaveValue("");
  await expect(page.getByLabel("Range end year")).toHaveValue("");
  const periodStrip = page.getByTestId("historical-period-strip");
  await expect(periodStrip).toContainText("PERIOD: MAX");
  await expect(periodStrip.getByLabel("Period dropdown")).toHaveValue("max");
  await expect(periodStrip.getByRole("button", { name: "Set period MAX" })).toHaveAttribute("aria-pressed", "true");
  await periodStrip.getByRole("button", { name: "Set period 2Y" }).click();
  await expect(periodStrip).toContainText("PERIOD: 2Y");
  await expect(periodStrip).toContainText("2Y |");
  await expect(periodStrip.getByLabel("Period dropdown")).toHaveValue("2");
  await expect(page.getByText("Displayed range", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("Range start year")).toHaveValue("2023");
  await expect(page.getByLabel("Range end year")).toHaveValue("2024");
  await periodStrip.getByLabel("Period dropdown").selectOption("4");
  await expect(periodStrip).toContainText("PERIOD: 4Y");
  await expect(page.getByLabel("Range start year")).toHaveValue("2021");
  await expect(page.getByLabel("Range end year")).toHaveValue("2024");
  await periodStrip.getByRole("button", { name: "Choose dates" }).click();
  await expect(periodStrip).toContainText("PERIOD: CUSTOM");
  await expect(periodStrip.getByLabel("Period dropdown")).toHaveValue("custom");
  await expect(periodStrip.getByRole("button", { name: "Choose dates" })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Range start year").fill("2021");
  await page.getByLabel("Range end year").fill("2024");
  await expect(page.getByLabel("Historical range")).toHaveValue("custom");
  await periodStrip.getByRole("button", { name: "Set period MAX" }).click();
  await expect(page.getByLabel("Range start year")).toHaveValue("");
  await expect(page.getByLabel("Range end year")).toHaveValue("");
  const priceLine = page.getByTestId("price-line");
  await expect(priceLine).toBeVisible();
  await expect(page.getByTestId("metric-area-path")).toBeVisible();
  await expect(page.getByTestId("normal-multiple-line")).toBeVisible();
  await expect(page.getByTestId("current-valuation-line")).toBeVisible();
  await expect(page.getByTestId("fair-value-line")).toBeVisible();
  const chartLineToggles = page.locator(".line-toggles");
  await expect(chartLineToggles.getByRole("button", { name: /Current valuation/ })).toBeVisible();
  await expect(chartLineToggles.getByRole("button", { name: /Custom valuation/ })).toBeVisible();
  await expect(chartLineToggles.getByRole("button", { name: /Payout ratio/ })).toBeVisible();
  await expect(chartLineToggles.getByRole("button", { name: /Dividend yield/ })).toBeVisible();
  await expect(chartLineToggles.getByRole("button", { name: /Recession bands/ })).toBeVisible();
  await expect(page.getByTestId("custom-valuation-line")).toHaveCount(0);
  await expect(page.getByTestId("payout-ratio-line")).toBeVisible();
  await expect(page.getByTestId("dividend-yield-line")).toHaveCount(0);
  await expect(page.getByTestId("recession-band")).toBeVisible();
  await expect(page.getByTestId("chart-settings-toggle")).toHaveAttribute("aria-expanded", "true");
  const chartSettingsDrawer = page.getByTestId("chart-settings-drawer");
  await expect(chartSettingsDrawer).toContainText("Chart settings");
  await expect(chartSettingsDrawer).toContainText("Selected year");
  await expect(chartSettingsDrawer).toContainText("Normal window");
  await expect(chartSettingsDrawer).toContainText("Forecast mode");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-source-guard")).toContainText("source_trace");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-source-guard")).toContainText("deterministic display formulas");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-layer-ledger")).toContainText("Layer Ledger");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-layer-source-backed")).toContainText("Source-backed on");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-layer-forecast")).toContainText("Forecast assumptions");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-layer-audit-route")).toContainText("Every visible chart layer");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-layer-off")).toContainText("Off / locked");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-evidence-price")).toContainText("sec_fixture");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-evidence-forecast")).toContainText("forecast_custom");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-evidence-scenario-lines")).toContainText("display_overlay");
  await expect(chartSettingsDrawer.getByTestId("chart-settings-line-custom-valuation")).toHaveAttribute("aria-pressed", "false");
  await chartSettingsDrawer.getByTestId("chart-settings-line-custom-valuation").click();
  await expect(page.getByTestId("custom-valuation-line")).toBeVisible();
  await expect(chartSettingsDrawer.getByTestId("chart-settings-line-custom-valuation")).toHaveAttribute("aria-pressed", "true");
  await chartSettingsDrawer.getByTestId("chart-settings-line-custom-valuation").click();
  await expect(page.getByTestId("custom-valuation-line")).toHaveCount(0);
  await chartSettingsDrawer.getByTestId("chart-settings-open-audit").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.metric")).toBeVisible();
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(historicalHeading).toBeVisible();
  await expect(page.getByTestId("graph-key-ledger")).toBeVisible();
  await expect(page.getByTestId("chart-workflow-chips")).toContainText("Price vs fundamentals");
  await expect(page.getByTestId("chart-workflow-chips")).toContainText("1-5Y forecast runway");
  await expect(page.getByTestId("chart-workflow-chips")).toContainText("Source audit ready");
  await expect(page.getByTestId("historical-map-readout")).toContainText("Actual history");
  await expect(page.getByTestId("historical-map-readout")).toContainText("Forecast runway");
  await expect(page.getByTestId("historical-map-readout")).toContainText("Source trace");
  await expect(page.getByTestId("historical-map-readout")).toContainText("Selected method");
  await expect(page.getByTestId("historical-evidence-rail")).toContainText("Valuation Decision Rail");
  await expect(page.getByTestId("historical-decision-strip")).toContainText("Fair value");
  await expect(page.getByTestId("historical-decision-strip")).toContainText("Upside vs price");
  await expect(page.getByTestId("historical-decision-strip")).toContainText("Total CAGR");
  await expect(page.getByTestId("historical-decision-strip")).toContainText("Source status");
  await expect(page.getByTestId("historical-decision-card-fair-value")).toContainText("orange line");
  await expect(page.getByTestId("historical-decision-card-upside")).toContainText("price");
  await expect(page.getByTestId("historical-decision-card-source")).toContainText(/linked|missing/);
  await expect(page.getByTestId("historical-evidence-contract")).toContainText("Every plotted point opens source_trace");
  await expect(page.getByTestId("historical-source-contract-card")).toContainText("Row source contract");
  await expect(page.getByTestId("historical-source-contract-status")).toContainText(/storage-ready|source_trace gaps/);
  await expect(page.getByTestId("historical-source-contract-total")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("historical-source-contract-reported")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("historical-source-contract-forecast")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("historical-source-contract-missing")).toContainText(/missing|no missing/);
  await expect(page.getByTestId("historical-evidence-method")).toContainText("Method");
  await expect(page.getByTestId("historical-evidence-confidence")).toContainText("Confidence");
  await expect(page.getByTestId("historical-evidence-source-doc")).toContainText("source_document_id");
  await expect(page.getByTestId("historical-evidence-available-at")).toContainText("available_at");
  await expect(page.getByTestId("historical-evidence-formula")).toContainText("Formula");
  await expect(page.getByTestId("historical-evidence-waterfall")).toContainText("GAAP -> Adjusted waterfall");
  await expect(page.getByTestId("historical-evidence-export")).toContainText("Export");
  await expect(page.getByTestId("historical-evidence-export-svg")).toHaveAttribute("href", /\/api\/v1\/charts\/valuation-map\/AAPL\.svg/);
  await expect(page.getByTestId("historical-evidence-export-png")).toHaveAttribute("href", /\/api\/v1\/charts\/valuation-map\/AAPL\.png/);
  await page.getByTestId("historical-evidence-open-audit").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  const highLowStrip = page.getByTestId("historical-high-low-strip");
  await expect(highLowStrip).toContainText("High");
  await expect(highLowStrip).toContainText("Low");
  await expect(highLowStrip).toContainText("Source");
  await expect(highLowStrip).toContainText("source-traced price rows");
  await page.getByTestId("high-low-audit-2024-high").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.price/
  );
  await expect(page.getByTestId("chart-layer-audit-strip")).toContainText("Layer audit");
  await expect(page.getByTestId("chart-layer-audit-row-price")).toContainText("Price line");
  await expect(page.getByTestId("chart-layer-audit-row-price")).toContainText("price points");
  await expect(page.getByTestId("chart-layer-audit-row-metric-area")).toContainText("actual");
  await expect(page.getByTestId("chart-layer-audit-row-forecast")).toContainText("forecast years");
  await expect(page.getByTestId("chart-layer-audit-row-scenario-lines")).toContainText("visible overlays");
  await page.getByTestId("chart-layer-audit-inspect-price").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.price/
  );
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(page.getByTestId("graph-key-row-current-valuation").getByText(/Current valuation \d+\.\d+x/)).toBeVisible();
  await expect(page.getByTestId("graph-key-row-current-valuation").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-chart_key\.current_multiple/
  );
  await expect(page.getByTestId("graph-key-row-custom-valuation").getByText("off")).toBeVisible();
  await expect(page.getByTestId("graph-key-row-payout-ratio").getByText(/Payout ratio \d+\.\d+%/)).toBeVisible();
  await expect(page.getByTestId("graph-key-row-payout-ratio").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-chart_key\.payout_ratio_pct/
  );
  await expect(page.getByTestId("graph-key-row-dividend-yield").getByText("off")).toBeVisible();
  await expect(page.getByTestId("graph-key-row-recession-bands").getByText(/Recession bands \d+ bands/)).toBeVisible();
  await expect(page.getByTestId("graph-key-row-normal-multiple").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.normal_multiple/
  );
  await page.getByTestId("graph-key-inspect-normal-multiple").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.normal_multiple")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.normal_multiple/
  );
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: /Scenario lines/ })).toBeVisible();
  await expect(page.getByTestId("forecast-scenario-line")).toHaveCount(0);
  await expect(priceLine).toHaveJSProperty("tagName", "polyline");
  await expect(priceLine).toHaveAttribute("data-price-points", "5");
  const priceLineEvidence = await priceLine.evaluate((line) => {
    const style = window.getComputedStyle(line);
    const box = (line as SVGGraphicsElement).getBBox();
    const points = line.getAttribute("points")?.trim().split(/\s+/).length ?? 0;
    return {
      points,
      stroke: style.stroke,
      strokeWidth: Number.parseFloat(style.strokeWidth),
      width: box.width,
      height: box.height
    };
  });
  expect(priceLineEvidence.points).toBeGreaterThanOrEqual(5);
  expect(priceLineEvidence.stroke).toBe("rgb(20, 22, 26)");
  expect(priceLineEvidence.strokeWidth).toBeGreaterThanOrEqual(3);
  expect(priceLineEvidence.width).toBeGreaterThan(0);
  expect(priceLineEvidence.height).toBeGreaterThan(0);
  await expect(page.getByTestId("selected-audit-trace").getByText("Selected Source Trace")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.metric")).toBeVisible();
  await page.getByTestId("audit-cell-2024-dividend").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.dividend")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-valuation\.dividend/
  );
  await page.getByRole("button", { name: "Select 2020 return point" }).click();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.metric")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2020-valuation\.metric/
  );
  await expect(page.getByTestId("chart-audit-drawer")).toBeVisible();
  await expect(page.getByTestId("chart-audit-drawer-selected")).toContainText("valuation.metric");
  await expect(page.getByTestId("chart-audit-evidence-strip")).toContainText("Source doc");
  await expect(page.getByTestId("chart-audit-evidence-strip")).toContainText("Filing");
  await expect(page.getByTestId("chart-audit-evidence-strip")).toContainText("Available at");
  await expect(page.getByTestId("chart-audit-drawer-fact-dividend")).toContainText("Dividend");
  await page.getByTestId("chart-audit-drawer-fact-dividend").click();
  await expect(page.getByTestId("chart-audit-drawer-selected")).toContainText("valuation.dividend");
  await page.getByTestId("chart-audit-drawer-fact-metric").click();
  await expect(page.getByTestId("chart-audit-drawer-selected")).toContainText("valuation.metric");
  await page.getByTestId("chart-audit-open-workspace").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.locator(".audit-grid thead")).toContainText("Available at");
  await expect(page.getByTestId("selected-audit-trace")).toContainText("Available at");
  await expect(page.getByTestId("selected-audit-trace")).toContainText(/T00:00:00(?:Z|\+00:00)|T12:00:00(?:Z|\+00:00)/);
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await expect(historicalHeading).toBeVisible();
  await page.getByRole("button", { name: "Select 2024 return point" }).click();
  await expect(page.getByTestId("selection-return")).toBeVisible();
  await expect(page.getByTestId("selection-return").getByText("2020-2024")).toBeVisible();
  await expect(page.getByTestId("selection-return").getByText("Total CAGR", { exact: true })).toBeVisible();
  await expect(page.getByTestId("range-navigator")).toBeVisible();
  await expect(page.getByTestId("range-navigator-line")).toBeVisible();
  await expect(page.getByTestId("range-navigator-point-2020")).toHaveClass(/return-range/);
  await expect(page.getByTestId("range-navigator-point-2024")).toHaveAttribute("aria-current", "true");
  await page.getByTestId("range-navigator-point-2022").click();
  await expect(page.getByTestId("range-navigator-point-2022")).toHaveAttribute("aria-current", "true");
  await page.getByTestId("range-navigator-point-2024").click();
  await expect(page.getByTestId("selection-return").getByText("2022-2024")).toBeVisible();
  await expect(page.getByTestId("range-navigator-point-2022")).toHaveClass(/return-range/);
  await expect(page.getByTestId("range-navigator-point-2024")).toHaveClass(/return-range/);
  const dragStartBox = await page.getByTestId("range-navigator-point-2021").boundingBox();
  const dragEndBox = await page.getByTestId("range-navigator-point-2024").boundingBox();
  if (!dragStartBox || !dragEndBox) {
    throw new Error("range navigator drag handles are not measurable");
  }
  await page.mouse.move(dragStartBox.x + dragStartBox.width / 2, dragStartBox.y + dragStartBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(dragEndBox.x + dragEndBox.width / 2, dragEndBox.y + dragEndBox.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByTestId("selection-return").getByText("2021-2024")).toBeVisible();
  await expect(page.getByTestId("range-navigator-point-2021")).toHaveClass(/return-range/);
  await expect(page.getByTestId("range-navigator-point-2024")).toHaveClass(/return-range/);
  await expect(page.getByTestId("chart-crosshair-vline")).toBeVisible();
  await expect(page.getByTestId("chart-crosshair-hline")).toBeVisible();
  await expect(page.getByTestId("chart-hover-card")).toContainText("2024");
  await page.getByTestId("range-navigator-point-2022").hover();
  await expect(page.getByTestId("chart-hover-card")).toContainText("2022");
  await expect(page.getByTestId("chart-hover-card")).toContainText("Price");
  await expect(page.getByTestId("chart-hover-card")).toContainText("Metric");
  await expect(page.getByTestId("chart-hover-evidence")).toContainText("Source");
  await expect(page.getByTestId("chart-hover-evidence")).toContainText("Doc");
  await page.getByTestId("year-column-2022").click();
  await expect(page.getByTestId("chart-hover-card")).toContainText("2022");
  await page.getByTestId("year-column-2022").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("chart-hover-card")).toContainText("2023");
  await expect(page.getByTestId("range-navigator-point-2023")).toHaveAttribute("aria-current", "true");
  await page.getByTestId("year-column-2024").click();
  await expect(page.getByTestId("range-navigator-point-2024")).toHaveAttribute("aria-current", "true");
  await page.getByTestId("year-column-2023").focus();
  await page.keyboard.press("Shift+Enter");
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("valuation.metric")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2023-valuation\.metric/
  );
  await page.getByRole("button", { name: "Historical" }).click();
  await historicalHeading.scrollIntoViewIfNeeded();
  await page.getByTestId("year-column-2024").click();
  await expect(page.getByTestId("range-navigator-point-2024")).toHaveAttribute("aria-current", "true");
  await chartLineToggles.getByRole("button", { name: /Current valuation/ }).click();
  await expect(page.getByTestId("current-valuation-line")).toHaveCount(0);
  await chartLineToggles.getByRole("button", { name: /Current valuation/ }).click();
  await expect(page.getByTestId("current-valuation-line")).toBeVisible();
  await chartLineToggles.getByRole("button", { name: /Custom valuation/ }).click();
  await expect(page.getByTestId("custom-valuation-line")).toBeVisible();
  await expect(page.getByTestId("graph-key-row-custom-valuation").getByText("Custom valuation 18.0x")).toBeVisible();
  await expect(page.getByTestId("graph-key-row-custom-valuation").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-chart_key\.custom_multiple.*target_multiple=18/
  );
  await chartLineToggles.getByRole("button", { name: /Custom valuation/ }).click();
  await expect(page.getByTestId("custom-valuation-line")).toHaveCount(0);
  await expect(page.getByTestId("graph-key-row-custom-valuation").getByText("off")).toBeVisible();
  await chartLineToggles.getByRole("button", { name: /Dividend yield/ }).click();
  await expect(page.getByTestId("dividend-yield-line")).toBeVisible();
  await expect(page.getByTestId("graph-key-row-dividend-yield").getByText(/Dividend yield \d+\.\d+%/)).toBeVisible();
  await expect(page.getByTestId("graph-key-row-dividend-yield").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-chart_key\.dividend_yield_pct.*target_multiple=18/
  );
  await chartLineToggles.getByRole("button", { name: /Payout ratio/ }).click();
  await expect(page.getByTestId("payout-ratio-line")).toHaveCount(0);
  await expect(page.getByTestId("graph-key-row-payout-ratio").getByText("off")).toBeVisible();
  await chartLineToggles.getByRole("button", { name: /Recession bands/ }).click();
  await expect(page.getByTestId("recession-band")).toHaveCount(0);
  await expect(page.getByTestId("graph-key-row-recession-bands").getByText("off")).toBeVisible();
  await expect(page.getByRole("cell", { name: "GAAP net income" })).toBeVisible();
  await expect(page.locator(".trade-marker.buy").first()).toBeVisible();
  await expect(page.getByTestId("portfolio-trade-overlay-marker")).toBeVisible();
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  const forecastContract = page.getByTestId("forecast-p1-contract");
  await expect(forecastContract).toContainText("Figma Forecast P-1 contract");
  await expect(forecastContract).toContainText("valuation-map, adjusted/GAAP metric series, estimates snapshots, user assumptions, dividend policy, source_trace guard");
  await expect(forecastContract).toContainText("calculator mode switch, EPS growth inputs, target multiple input");
  await expect(forecastContract).toContainText("Consensus/user/formula/AI lanes separated");
  await expect(forecastContract).toContainText("inactive calculators show run-to-calculate");
  await expect(page.getByTestId("forecast-state-chips")).toContainText("no source rejected");
  await expect(page.getByTestId("forecast-source-contract-card")).toContainText("Forecast source contract");
  await expect(page.getByTestId("forecast-source-contract-status")).toContainText(/storage-ready|forecast source gaps/);
  await expect(page.getByTestId("forecast-source-contract-projections")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("forecast-source-contract-audit")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("forecast-source-contract-meta")).toContainText(/ready|pending/);
  await expect(page.getByTestId("forecast-source-contract-missing")).toContainText(/missing|no missing/);
  await expect(page.getByTestId("forecast-consensus-preflight")).toContainText("Consensus preflight");
  await expect(page.getByTestId("forecast-consensus-preflight-status")).toContainText(/source-backed consensus/);
  await expect(page.getByTestId("forecast-consensus-missing-years")).toContainText(/No production consensus snapshot loaded|Missing FY|\d+Y consensus runway loaded/);
  await expect(page.getByTestId("forecast-consensus-check-snapshots")).toContainText("Snapshot source");
  await expect(page.getByTestId("forecast-consensus-check-lineage")).toContainText("Trace anchor");
  await expect(page.getByTestId("forecast-consensus-command-workpaper")).toContainText("consensus-workpaper");
  await expect(page.getByTestId("forecast-consensus-command-workpaper")).toContainText("storage/imports/consensus_aapl_workpaper.md");
  await expect(page.getByTestId("forecast-consensus-command-template")).toContainText("export-consensus-template");
  await expect(page.getByTestId("forecast-consensus-command-template")).toContainText("AAPL");
  await expect(page.getByTestId("forecast-consensus-command-template")).toContainText("storage/imports/consensus_aapl.csv");
  await expect(page.getByTestId("forecast-consensus-command-validate")).toContainText("validate-consensus-csv");
  await expect(page.getByTestId("forecast-consensus-command-validate")).toContainText("storage/imports/consensus_aapl.csv");
  await expect(page.getByTestId("forecast-consensus-command-import")).toContainText("import-consensus-csv");
  await expect(page.getByTestId("forecast-consensus-command-import")).toContainText("storage/imports/consensus_aapl.csv");
  await expect(page.getByTestId("forecast-decision-rail")).toContainText("Forecast Decision Rail");
  await expect(page.getByTestId("forecast-decision-strip")).toContainText("Terminal return");
  await expect(page.getByTestId("forecast-decision-card-ai-guard")).toContainText("No LLM numbers");
  await expect(page.getByTestId("forecast-decision-audit-formula")).toContainText("custom EPS override");
  const workflow = page.getByTestId("forecast-underwriting-workflow");
  await expect(workflow).toContainText("1Y-5Y underwriting workflow");
  await expect(page.getByTestId("forecast-workflow-horizon")).toContainText(/\/5Y runway/);
  await expect(page.getByTestId("forecast-workflow-mode")).toContainText("custom");
  await expect(page.getByTestId("forecast-workflow-consensus")).toContainText("Consensus lane");
  await expect(page.getByTestId("forecast-workflow-manual")).toContainText("Manual EPS");
  await expect(page.getByTestId("forecast-workflow-formula")).toContainText("Formula output");
  await expect(page.getByTestId("forecast-workflow-scenarios")).toContainText("Scenario lines");
  await expect(page.getByTestId("forecast-workflow-ai")).toContainText("llm_generated_numbers=false");
  await expect(page.getByTestId("forecast-underwriting-gates")).toContainText("Inputs separated");
  await expect(page.getByTestId("forecast-underwriting-gates")).toContainText("Return calculated");
  await expect(page.getByTestId("forecast-underwriting-gates")).toContainText("Audit ready");
  await expect(page.getByTestId("forecast-gate-ai-boundary")).toContainText("No LLM numbers");
  await expect(page.getByTestId("forecast-gate-ai-boundary")).toContainText(/commentary only|not used/);
  await page.getByTestId("forecast-decision-open-audit").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast.total_return_cagr_pct")).toBeVisible();
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  await expect(page.getByTestId("forecast-source-targets")).toContainText("forecast EPS cell");
  await expect(page.getByTestId("forecast-source-targets")).toContainText("assumption row");
  await page.getByTestId("forecast-target-eps-cell").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast.metric")).toBeVisible();
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  await page.getByTestId("forecast-target-assumption-row").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast_assumption.formula")).toBeVisible();
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  const calculatorBoard = page.getByTestId("forecast-calculator-switchboard");
  await expect(calculatorBoard.getByText("Forecast calculators", { exact: true })).toBeVisible();
  await expect(page.getByTestId("forecast-calculator-card-estimates")).toBeVisible();
  await expect(page.getByTestId("forecast-calculator-card-normal_multiple")).toBeVisible();
  await expect(page.getByTestId("forecast-calculator-card-lt_growth")).toBeVisible();
  await expect(page.getByTestId("forecast-calculator-card-historical_cagr")).toBeVisible();
  await expect(page.getByTestId("forecast-calculator-card-custom")).toHaveAttribute("aria-pressed", "true");
  const estimatesCalculatorCard = page.locator(".forecast-calculator-card").filter({ has: page.getByTestId("forecast-calculator-card-estimates") });
  await expect(estimatesCalculatorCard).toContainText("run-to-calculate");
  await page.getByTestId("forecast-calculator-card-normal_multiple").click();
  await expect(page.getByLabel("Forecast mode")).toHaveValue("normal_multiple");
  await expect(page.getByTestId("forecast-calculator-card-normal_multiple")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("forecast-calculator-card-custom").click();
  await expect(page.getByLabel("Forecast mode")).toHaveValue("custom");
  await page.getByLabel("Forecast mode").selectOption("custom");
  await page.getByLabel("Forecast case").selectOption("high");
  await expect(page.getByLabel("Forecast case")).toHaveValue("high");
  await expect(page.getByText("Forecast case", { exact: true })).toBeVisible();
  await expect(page.getByText("Forecast evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("Consensus range", { exact: true })).toBeVisible();
  await expect(page.getByText("Revision ledger", { exact: true })).toBeVisible();
  await expect(page.getByText("Earnings revisions", { exact: true })).toBeVisible();
  const provenanceLanes = page.getByTestId("forecast-provenance-lanes");
  await expect(provenanceLanes.getByText("Forecast input lanes", { exact: true })).toBeVisible();
  await expect(page.getByTestId("forecast-lane-consensus")).toContainText("Consensus snapshots");
  await expect(page.getByTestId("forecast-lane-manual")).toContainText("User EPS overrides");
  await expect(page.getByTestId("forecast-lane-formula")).toContainText("Deterministic formulas");
  await expect(page.getByTestId("forecast-lane-ai")).toContainText("AI commentary");
  await expect(page.getByTestId("forecast-lane-ai")).toContainText("llm_generated_numbers=false");
  const assumptionLedger = page.getByTestId("forecast-assumption-ledger");
  await expect(assumptionLedger.getByText("Assumption ledger", { exact: true })).toBeVisible();
  await expect(assumptionLedger.getByRole("cell", { name: "Formula", exact: true })).toBeVisible();
  await expect(assumptionLedger.getByText("custom EPS override when provided; missing years use growth from previous metric")).toBeVisible();
  const caseTable = page.getByTestId("forecast-case-table");
  await expect(caseTable.getByText("Consensus case matrix", { exact: true })).toBeVisible();
  await expect(caseTable.getByRole("columnheader", { name: "Estimate EPS" })).toBeVisible();
  await expect(caseTable.getByRole("link", { name: /low consensus estimate audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast_snapshot\.low\.estimate_eps.*target_multiple=18/
  );
  await expect(caseTable.getByRole("row").filter({ hasText: "median" }).first()).toBeVisible();
  await expect(caseTable.getByRole("link", { name: /median consensus estimate audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast_snapshot\.median\.estimate_eps.*target_multiple=18/
  );
  await expect(caseTable.getByRole("link", { name: /high consensus estimate audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast_snapshot\.high\.estimate_eps.*target_multiple=18/
  );
  const caseComparison = page.getByTestId("forecast-case-comparison");
  await expect(caseComparison.getByText("Bear / Base / Bull comparison", { exact: true })).toBeVisible();
  await expect(caseComparison.getByRole("columnheader", { name: "Total CAGR" })).toBeVisible();
  await expect(caseComparison.getByRole("row").filter({ hasText: "Base" }).first()).toBeVisible();
  await expect(caseComparison.getByRole("link", { name: /median case target audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast_case\.median\.target_price.*target_multiple=18/
  );
  await page.getByTestId("forecast-case-inspect-median").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast_case.median.total_return_cagr_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast_case\.median\.total_return_cagr_pct.*target_multiple=18/
  );
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  const projectionTable = page.getByTestId("forecast-projection-table");
  await expect(projectionTable.getByText("Forecast return calculator", { exact: true })).toBeVisible();
  await expect(projectionTable.getByRole("columnheader", { name: "Dividend-incl CAGR" })).toBeVisible();
  await expect(projectionTable.getByRole("columnheader", { name: "MoS" })).toBeVisible();
  await expect(projectionTable.getByRole("columnheader", { name: "Audit" })).toBeVisible();
  await expect(projectionTable.getByRole("row").filter({ hasText: "2025E" }).first()).toBeVisible();
  await expect(projectionTable.getByRole("row").filter({ hasText: "user_input" }).first()).toBeVisible();
  await expect(projectionTable.getByRole("link", { name: /2025 price audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.price.*target_multiple=18/
  );
  await expect(projectionTable.getByRole("link", { name: /2025 price_cagr_pct audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.price_cagr_pct.*target_multiple=18/
  );
  await expect(projectionTable.getByRole("link", { name: /2025 total_return_cagr_pct audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.total_return_cagr_pct.*target_multiple=18/
  );
  await expect(projectionTable.getByRole("link", { name: /2025 margin_of_safety_pct audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.margin_of_safety_pct.*target_multiple=18/
  );
  await page.getByTestId("forecast-projection-inspect-2025-margin-of-safety-pct").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast.margin_of_safety_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.margin_of_safety_pct.*target_multiple=18/
  );
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  await page.getByTestId("forecast-projection-inspect-2025-total-return-cagr-pct").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast.total_return_cagr_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2025-forecast\.total_return_cagr_pct.*target_multiple=18/
  );
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  await expect(page.getByText("Analyst Sentiment", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Year 1 EPS")).toBeVisible();
  await expect(page.getByTestId("forecast-scenario-line")).toBeVisible();
  await expect(page.getByTestId("forecast-scenario-label-18x")).toBeVisible();
  const scenarioWorkbench = page.getByTestId("forecast-scenario-workbench");
  await expect(scenarioWorkbench.getByText("Scenario line workbench", { exact: true })).toBeVisible();
  await expect(scenarioWorkbench.getByRole("columnheader", { name: "Terminal" })).toBeVisible();
  await expect(scenarioWorkbench.getByRole("columnheader", { name: "Total CAGR" })).toBeVisible();
  await expect(scenarioWorkbench.getByRole("link", { name: /18x terminal scenario audit/ })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2029-forecast_scenario\.18x\.target_price.*target_multiple=18/
  );
  await page.getByTestId("scenario-workbench-inspect-18x").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByText("forecast_scenario.18x.target_price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2029-forecast_scenario\.18x\.target_price.*target_multiple=18/
  );
  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("heading", { name: "Forecast Calculator" })).toBeVisible();
  const centerScenarioToggle = page.getByRole("button", { name: "Toggle 18x scenario line" });
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "true");
  await centerScenarioToggle.click();
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("forecast-scenario-label-18x")).toHaveCount(0);
  await expect(scenarioWorkbench.getByRole("button", { name: "Show 18x line from scenario workbench" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("10 active lines")).toBeVisible();
  await scenarioWorkbench.getByRole("button", { name: "Show 18x line from scenario workbench" }).click();
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("forecast-scenario-label-18x")).toBeVisible();
  await expect(scenarioWorkbench.getByRole("button", { name: "Hide 18x line from scenario workbench" })).toHaveAttribute("aria-pressed", "true");
  await centerScenarioToggle.click();
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "false");
  await page.getByLabel("Layout name").fill("AAPL high case");
  await page.getByRole("button", { name: "Save layout" }).click();
  await expect(page.getByText("saved AAPL high case")).toBeVisible();
  await expect(page.getByLabel("Saved chart layout").locator("option", { hasText: "AAPL high case - AAPL" })).toBeAttached();
  await centerScenarioToggle.click();
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("11 active lines")).toBeVisible();
  await page.getByLabel("Saved chart layout").selectOption({ label: "Select layout" });
  await page.getByLabel("Saved chart layout").selectOption({ label: "AAPL high case - AAPL" });
  await expect(page.getByText("loaded AAPL high case")).toBeVisible();
  await expect(centerScenarioToggle).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("10 active lines")).toBeVisible();
  await expect(page.getByTestId("forecast-scenario-line")).toBeVisible();
  await page.getByRole("button", { name: /Scenario lines/ }).click();
  await expect(page.getByTestId("forecast-scenario-line")).toHaveCount(0);
  await page.getByRole("button", { name: /Scenario lines/ }).click();
  await expect(page.getByTestId("forecast-scenario-line")).toBeVisible();
  await expect(page.getByText(/active lines/)).toBeVisible();
  await page.getByRole("button", { name: "Summary" }).click();
  await expect(page.getByRole("heading", { name: "Company Terminal" })).toBeVisible();
  await page.getByRole("button", { name: "Performance" }).click();
  await expect(page.getByRole("heading", { name: "Performance" })).toBeVisible();
  await expect(page.getByTestId("performance-decision-rail")).toContainText("Performance Decision Rail");
  await expect(page.getByTestId("performance-decision-strip")).toContainText("Total return");
  await expect(page.getByTestId("performance-decision-card-audit-coverage")).toContainText(/\d+\/\d+/);
  await expect(page.getByTestId("performance-decision-audit-formula")).toContainText(/return|price/i);
  await page.getByTestId("performance-decision-open-audit").click();
  await expect(page.getByTestId("selected-audit-trace").getByText(/performance\.(total_return_pct|reinvested_total_return_pct)\./)).toBeVisible();
  const performanceContract = page.getByTestId("performance-p1-contract");
  await expect(performanceContract).toContainText("price_bars + dividends + metric series");
  await expect(performanceContract).toContainText("Benchmark");
  await expect(page.getByLabel("Performance benchmark")).toBeDisabled();
  await expect(page.getByLabel("Performance benchmark")).toHaveValue("No source-backed benchmark loaded");
  await expect(page.getByTestId("performance-state-chips")).toContainText("missing benchmark");
  await expect(page.getByTestId("performance-state-chips")).toContainText("no source rejected");
  await expect(page.getByTestId("performance-audit-targets")).toContainText("Return card");
  await performanceContract.getByRole("button", { name: "Return card" }).click();
  await expect(page.getByTestId("selected-audit-trace").getByText("performance.total_return_pct.2023")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-performance\.total_return_pct\.2023/
  );
  const performanceTable = page.getByRole("table", { name: "Performance return table" });
  await expect(performanceTable.getByRole("columnheader", { name: "Total CAGR" })).toBeVisible();
  await expect(performanceTable.getByRole("row").filter({ hasText: "fixture_non_production_performance" }).first()).toBeVisible();
  const reinvestToggle = page.getByRole("button", { name: "Reinvest Dividends" });
  await expect(reinvestToggle).toHaveAttribute("aria-pressed", "false");
  await page.getByTestId("performance-audit-cell-2020-2024-total_return_pct").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("performance.total_return_pct.2020")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-performance\.total_return_pct\.2020/
  );
  await expect(page.getByText("Performance trace", { exact: true })).toBeVisible();
  await reinvestToggle.click();
  await expect(reinvestToggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Reinvested", { exact: true })).toBeVisible();
  await expect(performanceTable.getByRole("columnheader", { name: "Reinvested divs" })).toBeVisible();
  await page.getByTestId("performance-audit-cell-2020-2024-reinvested_total_return_pct").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("performance.reinvested_total_return_pct.2020")).toBeVisible();
  await page.getByRole("button", { name: "Research Report" }).click();
  await expect(page.getByRole("heading", { name: "Research Report" })).toBeVisible();
  await expect(page.getByText("AAPL Source-Audited Research Report")).toBeVisible();
  const researchReportPanel = page.locator(".single-panel").filter({ has: page.getByRole("heading", { name: "Research Report" }) });
  const reportSections = researchReportPanel.getByRole("table", { name: "Research Report sections" });
  const reportEvidence = researchReportPanel.getByRole("table", { name: "Research Report evidence" });
  const externalResearchMetadata = researchReportPanel.getByRole("table", { name: "External research metadata" });
  await expect(reportSections.getByRole("columnheader", { name: "Primary bullets" })).toBeVisible();
  await expect(reportSections.getByRole("row").filter({ hasText: "premium_to_fair_value" }).first()).toBeVisible();
  await expect(reportEvidence.getByRole("columnheader", { name: "Source" })).toBeVisible();
  await expect(reportEvidence.getByRole("columnheader", { name: "Quality" })).toBeVisible();
  await expect(reportEvidence.getByRole("row").filter({ hasText: "Fair value" }).first()).toBeVisible();
  await expect(researchReportPanel.getByText("External research metadata", { exact: true })).toBeVisible();
  await expect(externalResearchMetadata.getByRole("columnheader", { name: "Trace" })).toBeVisible();
  await expect(externalResearchMetadata.getByText("Source-backed metadata not loaded")).toBeVisible();
  await expect(researchReportPanel.getByText("External research metadata trace", { exact: true })).toBeVisible();
  await page.getByTestId("research-report-audit-cell-Valuation-Valuation_gap").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("research_report.valuation_gap_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-research_report\.valuation_gap_pct/
  );
  await expect(researchReportPanel.getByText("Research Report trace", { exact: true })).toBeVisible();
  await expect(researchReportPanel.getByText("research_report_derived", { exact: true }).first()).toBeVisible();
  const exportCenter = researchReportPanel.getByLabel("Export Center");
  await expect(exportCenter.getByRole("link", { name: /Markdown export/ })).toHaveAttribute("href", /research-report\.md.*target_multiple=18/);
  await expect(exportCenter.getByRole("link", { name: /JSON bundle/ })).toHaveAttribute("href", /research-report\.json.*target_multiple=18/);
  await expect(exportCenter.getByRole("link", { name: /Data Audit CSV/ })).toHaveAttribute(
    "href",
    /data-audit\.csv.*target_multiple=18/
  );
  await expect(exportCenter.getByRole("link", { name: /Chart SVG/ })).toHaveAttribute("href", /valuation-map\/AAPL\.svg.*hidden_scenario_lines=18x/);
  await expect(exportCenter.getByRole("link", { name: /Chart PNG/ })).toHaveAttribute("href", /valuation-map\/AAPL\.png.*hidden_scenario_lines=18x/);
  await exportCenter.getByRole("button", { name: /Create chart run/ }).click();
  await expect(exportCenter.getByRole("link", { name: /Replay SVG/ })).toHaveAttribute("href", /valuation-map\/runs\/.+\.svg/);
  await expect(exportCenter.getByRole("link", { name: /Replay PNG/ })).toHaveAttribute("href", /valuation-map\/runs\/.+\.png/);
  const chartRunEvidence = researchReportPanel.getByLabel("Chart run evidence summary");
  await expect(chartRunEvidence.getByText("Methods")).toBeVisible();
  await expect(chartRunEvidence.getByText("fixture_non_production").first()).toBeVisible();
  await expect(chartRunEvidence.getByText("Documents")).toBeVisible();
  await expect(chartRunEvidence.getByText("Quality")).toBeVisible();
  await page.getByRole("button", { name: "Financials" }).click();
  await expect(page.getByRole("heading", { name: "Financials" })).toBeVisible();
  const financialsContract = page.getByTestId("financials-p1-contract");
  await expect(financialsContract).toContainText(
    "normalized_facts, derived_metrics, statement periods, quality_flags, source documents"
  );
  await expect(financialsContract).toContainText(
    "annual/quarterly/TTM toggle, reported/reconstructed toggle, per-share/common-size switch, cell audit"
  );
  await expect(financialsContract).toContainText("IS/BS/CF rows keep source_trace");
  await expect(page.getByTestId("financials-mode-controls")).toContainText("TTM");
  await expect(page.getByTestId("financials-state-chips")).toContainText("no source rejected");
  await expect(page.getByTestId("financials-source-targets")).toContainText("statement cell");
  await page.getByTestId("financials-target-ratio-card").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("financials.roe")).toBeVisible();
  await page.getByTestId("financials-target-chart-point").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("financials.fcf")).toBeVisible();
  await page.getByTestId("financials-target-source-document-row").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("financials.revenue")).toBeVisible();
  await page.getByTestId("financial-audit-cell-2024-fcf").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("financials.fcf")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-financials\.fcf/
  );
  await page.getByRole("button", { name: "Fun Graphs" }).click();
  await expect(page.getByRole("heading", { name: "Fun Graphs" })).toBeVisible();
  await expect(page.getByTestId("fun-graphs-line")).toBeVisible();
  await expect(page.getByRole("button", { name: /Revenue/ })).toBeVisible();
  await expect(page.getByRole("table", { name: "FUN Graphs source table" }).getByRole("columnheader", { name: "Statement" })).toBeVisible();
  await page.getByTestId("fun-graphs-audit-cell-2024-revenue").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("fun_graphs.revenue")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-fun_graphs\.revenue/
  );
  await expect(page.getByText("Fun Graphs trace", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Screener", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Screener" })).toBeVisible();
  const screenerFilters = page.getByLabel("Screener filters");
  const screenerContract = page.getByTestId("screener-p1-contract");
  await expect(screenerContract).toContainText("universe + latest metric values + quality flags");
  await expect(page.getByTestId("screener-coverage-badges")).toContainText("source traced");
  await expect(page.getByTestId("screener-state-chips")).toContainText("no source rejected");
  await expect(screenerContract.getByRole("button", { name: "Save snapshot" })).toBeDisabled();
  await expect(screenerContract).toContainText("reported filters stay separate from estimated filters");
  await expect(page.getByTestId("screener-class-ledger")).toBeVisible();
  await expect(page.getByTestId("screener-class-metric-to-value")).toContainText("Metric-to-value");
  await expect(page.getByTestId("screener-class-metric-to-metric")).toContainText("Metric-to-metric");
  await expect(page.getByTestId("screener-class-company-relative")).toContainText("Company-relative");
  await expect(screenerFilters.getByLabel("Max P/E")).toHaveValue("25");
  await expect(screenerFilters.getByLabel("Min ROE")).toHaveValue("0");
  await screenerFilters.getByLabel("Max P/E").fill("10");
  await expect(page.getByTestId("screener-class-metric-to-value")).toContainText("P/E <= 10");
  const screenerTable = page.getByRole("table", { name: "Screener results" });
  await expect(screenerTable.getByRole("columnheader", { name: "Rel threshold" })).toBeVisible();
  await expect(screenerTable.getByRole("columnheader", { name: "All" })).toBeVisible();
  await expect(screenerTable.getByRole("columnheader", { name: "Source" })).toBeVisible();
  await expect(page.getByTestId("screener-filter-AAPL-metric-to-value")).toBeVisible();
  await expect(page.getByTestId("screener-filter-AAPL-metric-to-metric")).toBeVisible();
  await expect(page.getByTestId("screener-filter-AAPL-company-relative")).toBeVisible();
  await page.getByTestId("screener-filter-AAPL-company-relative").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("screener.normal_pe")).toBeVisible();
  await page.getByTestId("screener-audit-cell-AAPL-per").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("screener.per")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-screener\.per/
  );
  await page.getByRole("button", { name: "Watchlist" }).click();
  await expect(page.getByRole("heading", { name: "Watchlist" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "AAPL", exact: true })).toBeVisible();
  await page.getByTestId("watchlist-audit-cell-AAPL-current_price").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("watchlist.current_price")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-watchlist\.current_price/
  );
  await expect(page.getByLabel("Watchlist ticker")).toHaveValue("MSFT");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("saved MSFT")).toBeVisible();
  const msftWatchRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: "MSFT" }) });
  await expect(msftWatchRow).toBeVisible();
  await msftWatchRow.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("removed MSFT")).toBeVisible();
  await page.getByRole("button", { name: "Portfolio", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Portfolio" })).toBeVisible();
  const portfolioContract = page.getByTestId("portfolio-p1-contract");
  await expect(portfolioContract).toContainText("CSV transactions, holdings, prices, dividends, FX, valuation_series, source_trace");
  await expect(portfolioContract).toContainText("CSV import, holding select, transaction marker toggle, XIRR view, audit click");
  await expect(page.getByTestId("portfolio-state-chips")).toContainText("CSV parse ready");
  await expect(page.getByTestId("portfolio-source-targets")).toContainText("transaction row");
  await expect(portfolioContract).toContainText("User-entered transactions are tagged manual");
  await expect(page.getByRole("table", { name: "Portfolio holdings" }).getByRole("columnheader", { name: "Source" })).toBeVisible();
  await expect(page.getByTestId("portfolio-transaction-timeline")).toContainText("Transaction overlay ledger");
  await expect(page.getByTestId("portfolio-transaction-timeline")).toContainText("2023-01-10");
  await expect(page.getByTestId("portfolio-transaction-timeline")).toContainText("buy");
  await page.getByTestId("portfolio-target-transaction-row").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("portfolio_transaction.2023-01-10.buy.1.price")).toBeVisible();
  await page.getByTestId("portfolio-allocation-card-Technology").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("portfolio.weight_pct")).toBeVisible();
  await page.getByTestId("portfolio-transaction-audit-cell-0").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("portfolio_transaction.2023-01-10.buy.1.price")).toBeVisible();
  await page.getByTestId("portfolio-audit-cell-AAPL-market_value").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("portfolio.market_value")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-portfolio\.market_value/
  );
  const portfolioCsv = page.getByRole("textbox", { name: "Portfolio CSV" });
  await expect(portfolioCsv).toHaveValue(/AAPL/);
  await portfolioCsv.fill("date,ticker,side,quantity,price,currency,sector\n2024-01-02,AAPL,buy,1,100,USD,Technology");
  await page.getByRole("button", { name: "Import CSV" }).click();
  await expect(page.getByText("imported 1 holdings")).toBeVisible();
  await expect(page.getByTestId("portfolio-transaction-timeline")).toContainText("2024-01-02");
  await expect(page.getByText("Portfolio trace", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Use of Cash" }).click();
  await expect(page.getByRole("heading", { name: "Use of Cash" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Source" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Confidence" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Flags" })).toBeVisible();
  await page.getByTestId("use-of-cash-audit-cell-2024-free_cash_flow").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("use_of_cash.free_cash_flow")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-use_of_cash\.free_cash_flow/
  );
  await page.getByRole("button", { name: "Fiscal Fitness" }).click();
  await expect(page.getByRole("heading", { name: "Fiscal Fitness" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Category" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Value" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Quality" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: /profitability|cash_generation|solvency/ }).first()).toBeVisible();
  await page.getByTestId("fiscal-fitness-audit-cell-2024-roe_pct").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("fiscal_fitness.roe_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-fiscal_fitness\.roe_pct/
  );
  await page.getByRole("button", { name: "Health Check" }).click();
  await expect(page.getByRole("heading", { name: "Health Check" })).toBeVisible();
  await expect(page.getByText("FG Score", { exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Axis" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Predictability" })).toBeVisible();
  await page.getByTestId("health-check-audit-cell-overall_score").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("health_check.overall_score")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-health_check\.overall_score/
  );
  await page.getByTestId("health-check-audit-cell-predictability-table").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("health_check.predictability")).toBeVisible();
  await expect(page.getByText("Health Check trace", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Data Audit", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByText("Source facts", { exact: true })).toBeVisible();
  await expect(page.getByText("Derived rows", { exact: true })).toBeVisible();
  await expect(page.getByTestId("data-audit-source-guard")).toContainText("No source_trace = reject display");
  await expect(page.getByTestId("data-audit-workbench")).toContainText("Audit Workbench");
  await expect(page.getByTestId("data-audit-workbench-storage")).toContainText("Storage gate");
  await expect(page.getByTestId("data-audit-workbench-formula")).toContainText("Formula lineage");
  await expect(page.getByTestId("data-audit-workbench-adjusted-bridge")).toContainText("GAAP -> Adjusted bridge");
  await expect(page.getByTestId("data-audit-workbench-policy")).toContainText("Method / policy");
  await expect(page.getByTestId("data-audit-workbench-quality")).toContainText("Quality flags");
  await expect(page.getByTestId("selected-audit-summary-strip")).toContainText("Method");
  await expect(page.getByTestId("selected-audit-summary-source")).toContainText(/source|fixture|sec|opendart|warehouse/i);
  await expect(page.getByTestId("selected-audit-summary-period")).toContainText(/\d{4}|-/);
  await expect(page.getByTestId("selected-audit-summary-confidence")).toContainText(/\d|-/);
  await expect(page.getByTestId("selected-audit-summary-quality")).toContainText(/passed|source|fixture|warning|user_input|-/i);
  await expect(page.getByText("Macro & Industry Evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("Macro Series", { exact: true })).toBeVisible();
  await expect(page.getByText("Industry Series", { exact: true })).toBeVisible();
  await expect(page.getByText("Macro mode", { exact: true })).toBeVisible();
  await expect(page.getByText("Industry mode", { exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Source doc" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Formula" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "research_report.valuation_gap_pct" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "performance.total_return_pct" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "fun_graphs.revenue" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "analyst_scorecard.hit_rate_1y_pct" }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "forecast_assumption.formula" }).first()).toBeVisible();
  await expect(page.getByTestId("audit-namespace-ledger")).toBeVisible();
  await expect(page.getByTestId("audit-namespace-price_points")).toContainText("Price points");
  await expect(page.getByTestId("audit-namespace-forecast_snapshot")).toContainText("Consensus snapshots");
  await expect(page.getByTestId("audit-namespace-forecast_case")).toContainText("Case comparison");
  await expect(page.getByTestId("audit-namespace-forecast_scenario")).toContainText("Scenario lines");
  await expect(page.getByTestId("audit-namespace-portfolio_transaction")).toContainText("Transactions");
  await page.getByTestId("audit-namespace-forecast_snapshot").click();
  await expect(page.getByRole("row").filter({ hasText: "forecast_snapshot." }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "portfolio_transaction." })).toHaveCount(0);
  await page.getByRole("row").filter({ hasText: "forecast_snapshot." }).first().getByRole("button").first().click();
  await expect(page.getByTestId("selected-audit-trace").getByText(/forecast_snapshot\./)).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-20\d{2}-forecast_snapshot\..*target_multiple=18/
  );
  await page.getByTestId("audit-namespace-forecast_case").click();
  await expect(page.getByRole("row").filter({ hasText: "forecast_case." }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "portfolio_transaction." })).toHaveCount(0);
  await page.getByRole("row").filter({ hasText: "forecast_case." }).first().getByRole("button").first().click();
  await expect(page.getByTestId("selected-audit-trace").getByText(/forecast_case\./)).toBeVisible();
  await expect(page.getByTestId("data-audit-input-lineage")).toContainText("Input lineage");
  await expect(page.getByTestId("data-audit-input-lineage")).toContainText(/upstream trace|direct source fact/);
  await expect(page.getByTestId("data-audit-input-lineage")).toContainText(/Calculation Inputs|Forecast Snapshot Trace|Source Trace/i);
  await expect(page.getByTestId("audit-trace-section-input_traces")).toContainText(/forecast_snapshot_trace|calculation_inputs/);
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-20\d{2}-forecast_case\..*target_multiple=18/
  );
  await page.getByTestId("audit-namespace-forecast_scenario").click();
  await expect(page.getByRole("row").filter({ hasText: "forecast_scenario." }).first()).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "portfolio_transaction." })).toHaveCount(0);
  await page.getByRole("row").filter({ hasText: "forecast_scenario." }).first().getByRole("button").first().click();
  await expect(page.getByTestId("selected-audit-trace").getByText(/forecast_scenario\./)).toBeVisible();
  await expect(page.getByTestId("audit-trace-section-source_evidence")).toContainText("Source document");
  await expect(page.getByTestId("audit-trace-section-calculation")).toContainText("Formula");
  await expect(page.getByTestId("audit-trace-section-quality")).toContainText("Quality status");
  await expect(page.getByTestId("audit-trace-section-input_traces")).toContainText(/forecast_metric_trace|calculation_inputs/);
  await expect(page.getByTestId("selected-audit-raw-json")).toContainText(/forecast_metric_trace|calculation_inputs/);
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-20\d{2}-forecast_scenario\..*target_multiple=18/
  );
  await page.getByTestId("audit-namespace-portfolio_transaction").click();
  const portfolioTransactionRow = page.getByRole("row").filter({ hasText: "portfolio_transaction.2023-01-10.buy.1.price" });
  await expect(portfolioTransactionRow).toBeVisible();
  await portfolioTransactionRow.getByRole("button").first().click();
  await expect(page.getByTestId("selected-audit-trace").getByText("portfolio_transaction.2023-01-10.buy.1.price")).toBeVisible();
  await page.getByTestId("audit-namespace-price_points").click();
  const pricePointRow = page.getByRole("row").filter({ hasText: "price_point.close_price.2024-12-31" });
  await expect(pricePointRow).toBeVisible();
  await pricePointRow.getByRole("button").first().click();
  await expect(page.getByTestId("selected-audit-trace").getByText("price_point.close_price.2024-12-31")).toBeVisible();
  await page.getByTestId("audit-namespace-all").click();
  await expect(page.getByRole("row").filter({ hasText: "research_report.valuation_gap_pct" })).toBeVisible();
  await page.getByRole("button", { name: "Analyst Scorecard", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Analyst Scorecard" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "1Y Estimate" })).toBeVisible();
  await expect(page.getByText("fixture_non_production_scorecard_proxy").first()).toBeVisible();
  await page.getByTestId("analyst-scorecard-audit-cell-2024-error_1y_pct").click();
  await expect(page.getByTestId("selected-audit-trace").getByText("analyst_scorecard.error_1y_pct")).toBeVisible();
  await expect(page.getByTestId("selected-audit-trace").getByRole("link", { name: "Open fact" })).toHaveAttribute(
    "href",
    /\/api\/data-audit\/AAPL-2024-analyst_scorecard\.error_1y_pct/
  );
  await expect(page.getByText("Analyst Scorecard trace", { exact: true })).toBeVisible();
  const screenshot = await page.screenshot({ fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);
});

test("forecast tables remain usable on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.addStyleTag({ content: "nextjs-portal { pointer-events: none !important; }" });
  await page.getByTestId("quick-ticker-us-priority-AAPL").click();
  await expect(page.locator(".status-pill")).toHaveText(/API live|fixture fallback/);

  const mobileTabs = page.getByTestId("mobile-bottom-tabs");
  await expect(mobileTabs).toBeVisible();
  await mobileTabs.getByTestId("mobile-tab-Historical").click();
  await expect(page.getByRole("heading", { name: "Historical Valuation Map" })).toBeVisible();

  const mobileEvidence = page.getByTestId("mobile-evidence-summary");
  await expect(mobileEvidence).toBeVisible();
  await expect(mobileEvidence).toContainText("Method");
  await expect(mobileEvidence).toContainText("Confidence");
  await expect(mobileEvidence).toContainText("Source");
  await expect(mobileEvidence).toContainText("AAPL");

  const chartPan = page.getByTestId("mobile-chart-pan");
  const chartOverflow = await chartPan.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: window.getComputedStyle(element).overflowX
  }));
  expect(chartOverflow.scrollWidth).toBeGreaterThan(chartOverflow.clientWidth);
  expect(["auto", "scroll"]).toContain(chartOverflow.overflowX);

  await page.getByTestId("year-column-2024").click();
  await page.getByTestId("mobile-evidence-open-audit").click();
  await expect(page.getByRole("heading", { name: "Data Audit" })).toBeVisible();
  await expect(page.getByTestId("data-audit-source-guard")).toContainText("No source_trace = reject display");
  await page.getByTestId("data-audit-fact-AAPL-2024-valuation.metric").click();
  const mobileAuditDrawer = page.getByTestId("mobile-audit-drawer");
  await expect(mobileAuditDrawer).toBeVisible();
  await expect(mobileAuditDrawer).toContainText("Source Trace Inspector");
  await expect(mobileAuditDrawer).toContainText("Source doc");
  await expect(mobileAuditDrawer).toContainText("Formula lineage");
  await expect(page.getByTestId("mobile-audit-status")).toContainText("display allowed");
  await page.getByTestId("mobile-audit-drawer-close").click();
  await expect(page.getByTestId("mobile-audit-drawer")).toHaveCount(0);

  await mobileTabs.getByTestId("mobile-tab-Forecasting").click();

  const projectionTable = page.getByTestId("forecast-projection-table");
  const caseTable = page.getByTestId("forecast-case-table");
  await expect(projectionTable.getByText("Forecast return calculator", { exact: true })).toBeVisible();
  await expect(caseTable.getByText("Consensus case matrix", { exact: true })).toBeVisible();

  const projectionOverflow = await projectionTable.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: window.getComputedStyle(element).overflowX
  }));
  expect(projectionOverflow.scrollWidth).toBeGreaterThan(projectionOverflow.clientWidth);
  expect(["auto", "scroll"]).toContain(projectionOverflow.overflowX);
  await expect(projectionTable.getByRole("link", { name: /2025 total_return_cagr_pct audit/ })).toBeVisible();
});

test("ask-first entry routes valuation questions without stale ticker numbers", async ({ page }) => {
  await page.goto("/");

  const promptInput = page.getByLabel("Valuation question");
  await expect(promptInput).toHaveValue("Analyze 005930.KS adjusted EPS and 5Y downside");
  await expect(page.getByTestId("ask-underwriter-brief")).toContainText("005930.KS");
  await expect(page.getByTestId("ask-underwriter-answer")).toContainText("No valuation conclusion is shown until matching source-traced rows are loaded.");
  await expect(page.getByTestId("ask-underwriter-answer")).toContainText("stale_data_blocked");

  await promptInput.fill("AAPL AI review 5Y scenario notes");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.locator(".tabs button.active")).toHaveText("Forecasting");
  await expect(page.getByLabel("Forecast mode")).toHaveValue("ai_review");
  await expect(page.getByTestId("forecast-mini-card")).toContainText("ai_review");
  const aiReviewPanel = page.getByTestId("forecast-ai-review-panel");
  await expect(aiReviewPanel).toContainText("AI Review memo");
  await expect(aiReviewPanel).toContainText("AI review mode is commentary only");
  await expect(aiReviewPanel).toContainText("EPS, target price, and return values remain source-backed or deterministic");
  const traceGuard = page.getByTestId("forecast-trace-guard");
  await expect(traceGuard).toContainText("Source-trace guard");
  await expect(traceGuard).toContainText("deterministic_ai_review");
  await expect(traceGuard).toContainText("No LLM numbers");
  await expect(traceGuard).toContainText("commentary_only");
  const scenarioChart = page.getByTestId("forecast-scenario-chart");
  await expect(scenarioChart).toContainText("1Y-5Y scenario fan");
  await expect(scenarioChart.locator("svg")).toBeVisible();
  await expect(aiReviewPanel.getByRole("link").first()).toHaveAttribute("href", /\/api\/data-audit\/AAPL-2029-forecast\.total_return_cagr_pct/);

  await promptInput.fill("NVDA forecast 1Y-5Y source trace");
  await page.getByRole("button", { name: "Analyze" }).click();

  await expect(page.getByTestId("search-selected-ticker")).toContainText("NVDA");
  await expect(page.locator(".tabs button.active")).toHaveText("Forecasting");
  await expect(page.getByTestId("ask-underwriter-form")).toContainText("NVDA opened in Forecasting");
  await expect(page.getByTestId("ask-underwriter-form")).toContainText("5Y forecast");
  await expect(page.getByTestId("ask-underwriter-brief")).toContainText("NVIDIA data loading");
  await expect(page.getByTestId("ask-underwriter-brief")).not.toContainText("Apple Inc.");
  await expect(page.getByTestId("ask-underwriter-answer")).toContainText("No valuation conclusion is shown until matching source-traced rows are loaded.");
  await expect(page.getByTestId("ask-underwriter-answer")).toContainText("stale_data_blocked");

  await promptInput.fill("CRM consensus 3Y bull forecast target P/E 22 hide dividend floor line");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByTestId("search-selected-ticker")).toContainText("CRM");
  await expect(page.locator(".tabs button.active")).toHaveText("Forecasting");
  await expect(page.getByLabel("Forecast mode")).toHaveValue("consensus");
  await expect(page.getByLabel("Forecast case")).toHaveValue("high");
  await expect(page.locator("label").filter({ hasText: "Forecast" }).locator("input[type='range']")).toHaveValue("3");
  await expect(page.locator("label").filter({ hasText: "Target / custom P/E" }).getByRole("spinbutton")).toHaveValue("22");
  await expect(page.getByTestId("forecast-mini-card")).toContainText("Scenario lines");
  await expect(page.getByTestId("graph-key-row-dividend-floor")).toContainText("off");

  await promptInput.fill("AAPL custom 4Y manual EPS 6.70, 7.20, 7.90, 8.40 target P/E 19");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByTestId("search-selected-ticker")).toContainText("AAPL");
  await expect(page.locator(".tabs button.active")).toHaveText("Forecasting");
  await expect(page.getByLabel("Forecast mode")).toHaveValue("custom");
  await expect(page.locator("label").filter({ hasText: "Forecast" }).locator("input[type='range']")).toHaveValue("4");
  await expect(page.locator("label").filter({ hasText: "Target / custom P/E" }).getByRole("spinbutton")).toHaveValue("19");
  await expect(page.getByLabel("Year 1 EPS")).toHaveValue("6.70");
  await expect(page.getByLabel("Year 2 EPS")).toHaveValue("7.20");
  await expect(page.getByLabel("Year 3 EPS")).toHaveValue("7.90");
  await expect(page.getByLabel("Year 4 EPS")).toHaveValue("8.40");
  await expect(page.getByTestId("forecast-mini-card")).toContainText("Manual EPS");
  await expect(page.getByTestId("forecast-mini-card")).toContainText("4/4");
  await expect(page.getByTestId("forecast-lane-manual")).toContainText("4/4");
  await expect(page.getByTestId("forecast-lane-ai")).toContainText("commentary only");
  await expect(page.getByTestId("ask-underwriter-form")).toContainText("4/4 manual EPS overrides");

  await promptInput.fill("AAPL custom forecast 2025 EPS 6.60 2026 EPS 7.10");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByLabel("Forecast mode")).toHaveValue("custom");
  await expect(page.locator("label").filter({ hasText: "Forecast" }).locator("input[type='range']")).toHaveValue("2");
  await expect(page.getByLabel("Year 1 EPS")).toHaveValue("6.60");
  await expect(page.getByLabel("Year 2 EPS")).toHaveValue("7.10");
  await expect(page.getByTestId("forecast-mini-card")).toContainText("2/2");

  await page.keyboard.press("/");
  await expect(page.getByTestId("global-search-overlay")).toBeVisible();
  await expect(page.getByTestId("search-overlay-source-routing")).toContainText("No source_trace, no number");
  await expect(page.getByTestId("search-overlay-route-kr-priority")).toContainText("source required");
  await expect(page.getByTestId("search-overlay-route-us-jp")).toContainText("staged");
  await expect(page.getByTestId("search-overlay-route-data-audit")).toContainText("click-through");
  await page.getByTestId("global-search-input").fill("nvda");
  await expect(page.getByTestId("search-result-NVDA")).toContainText("NVIDIA");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("search-selected-ticker")).toContainText("NVDA");
  await page.getByTestId("global-search-trigger").click();
  await page.getByTestId("global-search-input").fill("aapl");
  await page.getByTestId("search-result-AAPL").click();
  await expect(page.getByTestId("search-selected-ticker")).toContainText("AAPL");
  await page.getByTestId("global-search-trigger").click();
  await expect(page.getByTestId("global-search-overlay").getByText("/ Search securities, portfolios, screens, source traces")).toBeVisible();
  await page.getByRole("button", { name: "Source traces" }).click();
  await page.getByTestId("global-search-input").fill("waterfall");
  await expect(page.getByTestId("search-result-adjusted-eps-waterfall")).toContainText("Adjusted EPS Waterfall");
  await page.getByTestId("search-result-adjusted-eps-waterfall").click();
  await expect(page.locator(".tabs button.active")).toHaveText("Data Audit");

  await page.getByRole("button", { name: "Build 1Y-5Y bear/base/bull forecast" }).click();
  await expect(page.locator(".tabs button.active")).toHaveText("Forecasting");
});

test("shareable research routes expose fail-closed consensus, peers, and provider states", async ({ page }) => {
  const unavailable = (status: "missing_source" | "missing_contract", reason: string) => ({
    data: null,
    state: { status, available: false, data_mode: "unavailable", reason },
    meta: { fixture_fallback_used: false }
  });

  await page.route("**/api/v1/companies/005930.KS/consensus", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(unavailable("missing_source", "No validated point-in-time consensus snapshot is available."))
    });
  });
  await page.route("**/api/v1/companies/005930.KS/peers?kind=*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(unavailable("missing_contract", "No validated peer-classification dataset is configured."))
    });
  });
  await page.route("**/api/v1/system/providers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          providers: [
            {
              provider_id: "opendart",
              label: "OpenDART",
              capabilities: ["kr_financial_statements"],
              contract_available: true,
              configured: false,
              verification: "configuration_only",
              required_env: ["OPENDART_API_KEY"],
              state: {
                status: "missing_key",
                available: false,
                data_mode: "unavailable",
                reason: "Required provider configuration is absent."
              }
            }
          ]
        },
        state: { status: "configured", available: true, data_mode: "configuration_only", reason: null },
        meta: { secret_values_exposed: false }
      })
    });
  });

  await page.goto("/company/005930.KS/consensus");
  await expect(page).toHaveURL(/\/terminal\?ticker=005930\.KS&tab=Consensus/);
  await expect(page.getByTestId("consensus-contract-panel")).toContainText("No source-backed values displayed");
  await expect(page.getByTestId("consensus-contract-panel")).toContainText("missing source");

  await page.getByRole("button", { name: "Peers", exact: true }).click();
  await expect(page.getByTestId("peers-contract-panel")).toContainText("missing contract");
  await expect(page.getByTestId("peers-contract-panel")).toContainText("No validated peer-classification dataset");

  await page.goto("/system");
  await expect(page).toHaveURL(/\/terminal\?tab=System/);
  await expect(page.getByTestId("provider-status-panel")).toContainText("OpenDART");
  await expect(page.getByTestId("provider-status-panel")).toContainText("missing_key");
  await expect(page.getByTestId("provider-status-panel")).not.toContainText("secret_values_exposed");
});
