const args = parseArgs(process.argv.slice(2));
const baseUrl = normalizeBaseUrl(args.baseUrl || process.env.SMOKE_BASE_URL || "http://127.0.0.1:8000");
const ticker = args.ticker || process.env.SMOKE_TICKER || "AAPL";
const cookie = args.cookie || process.env.PF_SESSION_COOKIE || "";
const timeoutMs = Number(args.timeoutMs || process.env.SMOKE_TIMEOUT_MS || 15000);
const requireConsensusForecast = parseBoolean(
  args.requireConsensusForecast || process.env.SMOKE_REQUIRE_CONSENSUS_FORECAST || "false"
);
const requireKrTop10ProductionGate = parseBoolean(
  args.requireKrTop10ProductionGate || process.env.SMOKE_REQUIRE_KR_TOP10_PRODUCTION_GATE || "false"
);
const requireKrTop10PartialAudit = parseBoolean(
  args.requireKrTop10PartialAudit || process.env.SMOKE_REQUIRE_KR_TOP10_PARTIAL_AUDIT || "false"
);
const krTop10Tickers = normalizeTickerList(
  args.krTop10Tickers ||
  process.env.SMOKE_KR_TOP10_TICKERS ||
  "005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS"
);
const expectedKrTop10PartialTickers = normalizeTickerList(
  args.expectKrTop10PartialTickers ||
  process.env.SMOKE_EXPECT_KR_TOP10_PARTIAL_TICKERS ||
  "",
  { allowEmpty: true }
);

if (requireKrTop10ProductionGate && (requireKrTop10PartialAudit || expectedKrTop10PartialTickers.length > 0)) {
  console.error("--require-kr-top10-production-gate cannot be combined with KR partial audit smoke flags.");
  process.exit(2);
}

const checks = [
  {
    name: "api_health",
    path: "/api/health",
    type: "json",
    validate: (payload) => Boolean(payload)
  },
  {
    name: "source_readiness",
    path: "/api/v1/system/readiness",
    type: "json",
    validate: (payload) => typeof payload?.status === "string"
  },
  {
    name: "source_coverage",
    path: `/api/v1/system/source-coverage?tickers=${encodeURIComponent(ticker)}${requireConsensusForecast ? "&require_consensus_forecast=true" : ""}`,
    type: "json",
    validate: (payload) => {
      const rows = Array.isArray(payload?.tickers) ? payload.tickers : [];
      const tickerRow = rows.find((row) => row?.ticker === String(ticker).toUpperCase());
      const consensusReady = !requireConsensusForecast || (
        tickerRow?.consensus_forecast_ready === true &&
        !((payload?.summary?.missing_consensus_forecast || []).includes(String(ticker).toUpperCase()))
      );
      return (
        typeof payload?.status === "string" &&
        typeof payload?.data_mode === "string" &&
        typeof payload?.summary?.tickers_expected === "number" &&
        Boolean(tickerRow?.counts) &&
        Boolean(tickerRow?.method_counts) &&
        consensusReady
      );
    }
  },
  {
    name: "industry_series",
    path: "/api/v1/industry-series?limit=5",
    type: "json",
    validate: (payload) => (
      Array.isArray(payload?.data) &&
      typeof payload?.meta?.data_mode === "string" &&
      typeof payload?.meta?.quality_status === "string"
    )
  },
  {
    name: "macro_series",
    path: "/api/v1/macro-series?limit=5",
    type: "json",
    validate: (payload) => (
      Array.isArray(payload?.data) &&
      typeof payload?.meta?.data_mode === "string" &&
      typeof payload?.meta?.quality_status === "string"
    )
  },
  {
    name: "security_search",
    path: `/api/v1/securities/search?q=${encodeURIComponent(ticker)}`,
    type: "json",
    validate: (payload) => Array.isArray(payload?.data)
  },
  {
    name: "valuation_map_adjusted_forecast",
    path: `/api/v1/companies/${encodeURIComponent(ticker)}/valuation-map?metric=adjusted_operating&forecast_years=5`,
    type: "json",
    validate: (payload) => {
      const rows = Array.isArray(payload?.data) ? payload.data : [];
      const forecastRows = rows.filter((row) => row.forecast_flag === true);
      const consensus = payload?.meta?.forecast?.consensus || {};
      const sourceBackedForecastOk = !requireConsensusForecast || (
        payload?.meta?.forecast?.source === "consensus_snapshot" &&
        !String(consensus.quality_status || "").startsWith("fixture_") &&
        !forecastRows.some((row) => String(row?.source_trace?.quality_status || "").startsWith("fixture_"))
      );
      return (
        rows.length > 0 &&
        forecastRows.length > 0 &&
        rows.every((row) => row.source_trace) &&
        typeof payload?.meta?.forecast?.formula === "string" &&
        typeof consensus.quality_status === "string" &&
        sourceBackedForecastOk
      );
    }
  },
  {
    name: "forecast_snapshots",
    path: `/api/v1/companies/${encodeURIComponent(ticker)}/forecast-snapshots`,
    type: "json",
    validate: (payload) => {
      const data = payload?.data || {};
      const trace = data?.source_trace || {};
      const sourceBackedOk = !requireConsensusForecast || (
        data?.meta?.data_mode === "source_backed" &&
        !String(trace.quality_status || "").startsWith("fixture_")
      );
      return (
        Array.isArray(data?.cases) &&
        Array.isArray(data?.revisions) &&
        typeof data?.sentiment?.label === "string" &&
        typeof data?.scorecard?.summary?.required_source === "string" &&
        typeof trace?.quality_status === "string" &&
        sourceBackedOk
      );
    }
  },
  {
    name: "chart_svg",
    path: `/api/v1/charts/valuation-map/${encodeURIComponent(ticker)}.svg?metric=adjusted_operating&forecast_years=3`,
    type: "svg",
    validate: (body) => body.includes("<svg")
  },
  {
    name: "chart_png",
    path: `/api/v1/charts/valuation-map/${encodeURIComponent(ticker)}.png?metric=adjusted_operating&forecast_years=3`,
    type: "png",
    validate: (body) => body.length >= 8 && body[0] === 0x89 && body[1] === 0x50 && body[2] === 0x4e && body[3] === 0x47
  }
];

if (requireKrTop10ProductionGate || requireKrTop10PartialAudit || expectedKrTop10PartialTickers.length > 0) {
  const krTop10TickerQuery = encodeURIComponent(krTop10Tickers.join(","));
  checks.push({
    name: "kr_top10_valuation_cache",
    path: `/api/v1/system/kr-valuation-cache-coverage?tickers=${krTop10TickerQuery}`,
    type: "json",
    validate: async (payload) => {
      const rows = Array.isArray(payload?.rows) ? payload.rows : [];
      const expected = krTop10Tickers.length;
      const baseOk = (
        payload?.market === "KR" &&
        typeof payload?.source_trace?.source_document_id === "string" &&
        payload?.summary?.tickers_expected === expected &&
        rows.length === expected
      );
      if (!baseOk) {
        return { ok: false, detail: "kr_top10_cache_base_contract_failed" };
      }
      if (requireKrTop10ProductionGate) {
        const ok = (
          payload?.summary?.cache_files_found === expected &&
          payload?.summary?.valuation_ready === expected &&
          payload?.summary?.financial_numbers_allowed === expected &&
          rows.every((row) => (
            row?.cache_found === true &&
            row?.valuation_ready === true &&
            row?.financial_numbers_allowed === true &&
            typeof row?.coverage_status === "string"
          ))
        );
        return {
          ok,
          detail: ok ? "passed" : "kr_top10_production_cache_not_ready"
        };
      }

      const targetRows = expectedKrTop10PartialTickers.length > 0
        ? expectedKrTop10PartialTickers.map((expectedTicker) => rows.find((row) => row?.ticker === expectedTicker))
        : rows.filter((row) => row?.coverage_status === "partial_source_backed");
      const ok = (
        targetRows.length > 0 &&
        targetRows.every((row) => row && isKrPartialGapAuditRow(row))
      );
      if (!ok) {
        return {
          ok: false,
          detail: krPartialAuditFailureDetail(rows, targetRows, expectedKrTop10PartialTickers)
        };
      }
      return verifyKrPartialGapAuditFacts(targetRows);
    }
  });
}

if (requireKrTop10ProductionGate) {
  const krTop10TickerQuery = encodeURIComponent(krTop10Tickers.join(","));
  checks.push(
    {
      name: "kr_top10_production_source_coverage",
      path: `/api/v1/system/source-coverage?market=KR&tickers=${krTop10TickerQuery}&require_consensus_forecast=true`,
      type: "json",
      validate: (payload) => {
        const rows = Array.isArray(payload?.tickers) ? payload.tickers : [];
        const expected = krTop10Tickers.length;
        const expectedSet = new Set(krTop10Tickers);
        return (
          payload?.status === "ready" &&
          payload?.postgres?.reachable === true &&
          payload?.summary?.tickers_expected === expected &&
          payload?.summary?.core_ready === expected &&
          payload?.summary?.consensus_forecast_ready === expected &&
          Array.isArray(payload?.summary?.missing_core) &&
          payload.summary.missing_core.length === 0 &&
          Array.isArray(payload?.summary?.missing_consensus_forecast) &&
          payload.summary.missing_consensus_forecast.length === 0 &&
          rows.length === expected &&
          rows.every((row) => (
            expectedSet.has(row?.ticker) &&
            row?.core_ready === true &&
            row?.consensus_forecast_ready === true &&
            Array.isArray(row?.missing_required) &&
            row.missing_required.length === 0
          ))
        );
      }
    }
  );
}

const results = [];
for (const check of checks) {
  results.push(await runCheck(check));
}

const failures = results.filter((result) => !result.ok);
const summary = {
  status: failures.length === 0 ? "ok" : "failed",
  base_url: baseUrl,
  ticker,
  checks: results
};
console.log(JSON.stringify(summary, null, 2));

if (failures.length > 0) {
  process.exitCode = 1;
}

async function runCheck(check) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const url = `${baseUrl}${check.path}`;
  const startedAt = Date.now();
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        Accept: check.type === "json" ? "application/json" : "*/*",
        ...(cookie ? { Cookie: cookie } : {})
      }
    });
    const elapsedMs = Date.now() - startedAt;
    if (!response.ok) {
      return {
        name: check.name,
        ok: false,
        status: response.status,
        elapsed_ms: elapsedMs,
        detail: response.status === 401 ? "authentication_required_set_PF_SESSION_COOKIE" : "http_error"
      };
    }
    const body = check.type === "json" ? await response.json() : new Uint8Array(await response.arrayBuffer());
    const normalizedBody = check.type === "svg" ? new TextDecoder().decode(body) : body;
    const validation = normalizeValidationResult(await check.validate(normalizedBody));
    return {
      name: check.name,
      ok: validation.ok,
      status: response.status,
      elapsed_ms: elapsedMs,
      detail: validation.ok ? "passed" : validation.detail
    };
  } catch (error) {
    return {
      name: check.name,
      ok: false,
      status: null,
      elapsed_ms: Date.now() - startedAt,
      detail: error?.name === "AbortError" ? "timeout" : String(error?.message || error)
    };
  } finally {
    clearTimeout(timer);
  }
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--base-url") {
      parsed.baseUrl = requiredValue(argv, index, arg);
      index += 1;
    } else if (arg === "--ticker") {
      parsed.ticker = requiredValue(argv, index, arg);
      index += 1;
    } else if (arg === "--cookie") {
      parsed.cookie = requiredValue(argv, index, arg);
      index += 1;
    } else if (arg === "--timeout-ms") {
      parsed.timeoutMs = requiredValue(argv, index, arg);
      index += 1;
    } else if (arg === "--require-consensus-forecast") {
      parsed.requireConsensusForecast = "true";
    } else if (arg === "--require-kr-top10-production-gate") {
      parsed.requireKrTop10ProductionGate = "true";
    } else if (arg === "--require-kr-top10-partial-audit") {
      parsed.requireKrTop10PartialAudit = "true";
    } else if (arg === "--kr-top10-tickers") {
      parsed.krTop10Tickers = requiredValue(argv, index, arg);
      index += 1;
    } else if (arg === "--expect-kr-top10-partial-tickers") {
      parsed.expectKrTop10PartialTickers = requiredValue(argv, index, arg);
      index += 1;
    } else {
      console.error(`Unknown argument: ${arg}`);
      process.exit(2);
    }
  }
  return parsed;
}

function requiredValue(argv, index, flag) {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) {
    console.error(`${flag} requires a value.`);
    process.exit(2);
  }
  return value;
}

function normalizeBaseUrl(value) {
  return String(value).replace(/\/+$/, "");
}

function parseBoolean(value) {
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function normalizeTickerList(value, options = {}) {
  const seen = new Set();
  const tickers = String(value)
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter((item) => {
      if (!item || seen.has(item)) {
        return false;
      }
      seen.add(item);
      return true;
    });
  if (!tickers.length && !options.allowEmpty) {
    console.error("--kr-top10-tickers must include at least one ticker.");
    process.exit(2);
  }
  return tickers;
}

function normalizeValidationResult(result) {
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
    return {
      ok: Boolean(result.ok),
      detail: String(result.detail || "response_contract_failed")
    };
  }
  return {
    ok: Boolean(result),
    detail: "response_contract_failed"
  };
}

function krPartialAuditFailureDetail(rows, targetRows, expectedTickers) {
  const expectedLabel = expectedTickers.length > 0 ? expectedTickers.join(",") : "any_partial_source_backed";
  const partialTickers = rows
    .filter((row) => row?.coverage_status === "partial_source_backed")
    .map((row) => row?.ticker)
    .filter(Boolean);
  const invalidOrMissing = targetRows
    .map((row, index) => {
      if (row && isKrPartialGapAuditRow(row)) {
        return null;
      }
      return row?.ticker || expectedTickers[index] || `missing_target_${index + 1}`;
    })
    .filter(Boolean);
  return [
    "kr_partial_audit_failed",
    `expected=${expectedLabel}`,
    `partial=${partialTickers.join(",") || "none"}`,
    `invalid_or_missing=${invalidOrMissing.join(",") || "none"}`
  ].join(" ");
}

async function verifyKrPartialGapAuditFacts(rows) {
  const factIds = rows
    .flatMap((row) => (Array.isArray(row?.gap_audit_refs) ? row.gap_audit_refs : []))
    .map((ref) => ref?.fact_id)
    .filter(Boolean)
    .slice(0, 8);
  const failures = [];
  for (const factId of factIds) {
    const result = await fetchDataAuditFact(factId);
    if (!result.ok) {
      failures.push(`${factId}:${result.detail}`);
    }
  }
  return {
    ok: failures.length === 0,
    detail: failures.length === 0
      ? "passed"
      : `kr_partial_audit_fact_unresolved ${failures.join(",")}`
  };
}

async function fetchDataAuditFact(factId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const path = `/api/data-audit/${encodeURIComponent(factId)}?forecast_years=1`;
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(cookie ? { Cookie: cookie } : {})
      }
    });
    if (!response.ok) {
      return { ok: false, detail: `http_${response.status}` };
    }
    const payload = await response.json();
    const row = payload?.data || {};
    const trace = row?.source_trace || {};
    const ok = (
      row?.fact_id === factId &&
      typeof trace?.source_document_id === "string" &&
      typeof trace?.source_type === "string" &&
      typeof trace?.method === "string" &&
      typeof trace?.quality_status === "string"
    );
    return {
      ok,
      detail: ok ? "passed" : "fact_trace_contract_failed"
    };
  } catch (error) {
    return {
      ok: false,
      detail: error?.name === "AbortError" ? "timeout" : String(error?.message || error)
    };
  } finally {
    clearTimeout(timer);
  }
}

function isKrPartialGapAuditRow(row) {
  const refs = Array.isArray(row?.gap_audit_refs) ? row.gap_audit_refs : [];
  return (
    row?.coverage_status === "partial_source_backed" &&
    row?.valuation_ready === true &&
    row?.financial_numbers_allowed === true &&
    refs.length > 0 &&
    refs.every((ref) => (
      typeof ref?.scope === "string" &&
      typeof ref?.fiscal_year === "number" &&
      typeof ref?.status === "string" &&
      typeof ref?.fact_name === "string" &&
      typeof ref?.fact_id === "string" &&
      typeof ref?.label === "string" &&
      typeof ref?.source_document_id === "string" &&
      typeof ref?.source_type === "string" &&
      typeof ref?.method === "string" &&
      typeof ref?.quality_status === "string" &&
      typeof ref?.reason === "string"
    ))
  );
}
