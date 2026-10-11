import {marketFetch} from "./public-transport";

export type Trace = {
  source: string;
  source_url: string;
  period: string;
  unit: string;
  method: string;
  formula: string;
  ingested_at?: string;
  scope?: string;
  quality_flags?: string[];
};
export type Fact = {
  value: number | null;
  period: string;
  unit: string;
  source_trace?: Trace;
};
export type Metric = {
  id: string;
  label: string;
  unit: string;
  definition: string;
  formula: string;
  period: string;
  caution: string;
  example: string;
};
export type Stock = {
  logo_url?: string | null;
  code: string;
  name: string;
  market: string;
  sector?: string;
  price: number | null;
  change: number | null;
  volume: number | null;
  value: number | null;
  cap: number | null;
  foreign: number | null;
  per: number | null;
  pbr: number | null;
  roe: number | null;
  dividend: number | null;
  foreign_net: number | null;
  institution_net: number | null;
  revenue: number | null;
  operating_profit: number | null;
  as_of: string;
  received_at: string;
  market_status: string;
  source_trace?: Trace;
  facts: Record<string, Fact>;
};
export type MetricId =
  | "price"
  | "change"
  | "volume"
  | "value"
  | "cap"
  | "foreign"
  | "per"
  | "pbr"
  | "roe"
  | "dividend"
  | "foreign_net"
  | "institution_net"
  | "revenue"
  | "operating_profit";
export type Rule = {
  metric: MetricId;
  operator: "gte" | "lte" | "eq";
  value: number;
};
export type Query = {
  q: string;
  sector?: string;
  market: string;
  rules: Rule[];
  any_rules: Rule[];
  sort: MetricId;
  direction: "asc" | "desc";
};
export type View = {
  id: string;
  name: string;
  query: Query;
  columns: MetricId[];
  widths: Record<string, number>;
};
export type Preferences = {
  version: 1;
  favorites: string[];
  groups: Record<string, string[]>;
  recent: string[];
  views: View[];
  columns: MetricId[];
  widths: Record<string, number>;
  theme: "system" | "light" | "dark";
  large: boolean;
  textSize?: "normal" | "large" | "larger";
  compact: boolean;
  home: string[];
  startPage?: "market" | "watchlist";
  sidebarHidden?: boolean;
};
export type Overview = {
  sectors?: { name: string; count: number; change: number; value: number }[];
  indices: {
    code: string;
    name: string;
    price: number;
    change: number;
    as_of: string;
    market_status: string;
    source_trace: Trace;
  }[];
  breadth: { up: number; down: number; flat: number; covered: number };
  total: number;
  value: number;
  received_at: string;
  scope: string;
  unavailable_markets: string[];
  worker: { status?: string; updated_at?: string };
  filings?: {
    rows: {
      corp_name: string;
      corp_cls?: string | null;
      title: string;
      date: string;
      receipt: string;
      url: string;
    }[];
    status: "ready" | "stale" | "unavailable" | string;
    received_at?: string;
    total?: number;
    scope?: string;
  };
};
export type Price = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
export type Financial = {
  name: string;
  period: string;
  frequency: string;
  basis: string;
  estimate: boolean;
  value: number | null;
  unit: string;
  formula?: string;
  source_trace: Trace;
};
export type CollectionSource = {
  status: "ready" | "partial" | "stale" | "no_data" | "unavailable" | "error" | "rate_limited" | "pending" | string;
  source?: string | null;
  received_at?: string | null;
  attempted_at?: string | null;
  failed_at?: string | null;
  as_of?: string | null;
  note?: string | null;
};
export type CoverageSnapshot = {
  total: number;
  attempted: number;
  visited: number;
  remaining: number;
  updated_at: string;
  universe_received_at?: string;
  scope: string;
  sources: Record<
    string,
    {
      label: string;
      total: number;
      covered: number;
      ready: number;
      partial: number;
      stale: number;
      no_data: number;
      unavailable: number;
      error: number;
      rate_limited: number;
      pending: number;
    }
  >;
  metric_coverage: Record<string, number>;
  market_lists: { market: string; received: number; source_rows_received?: number; source_rows_expected?: number; complete: boolean; scope?: string }[];
  unavailable_markets: string[];
  worker: { status?: string; updated_at?: string };
};
export type Detail = {
  company?: { products?: string; listed_at?: string };
  official?: {
    company: Record<string, string | null>;
    report_facts?: {
      year: string;
      report_code: string;
      sections: Partial<Record<"shareholders" | "dividends" | "shares" | "employees" | "executives" | "minority_shareholders" | "holder_changes" | "auditor" | "treasury" | "investments", {
        status: string;
        year: string;
        rows: Record<string, string | number | null>[];
        as_of?: string | null;
      }>>;
    };
    source_trace?: Trace;
    financials_received_at?: string;
    full_financials_periods?: string[];
    major_accounts_received_at?: string;
    major_accounts_period?: string;
    major_accounts_status?: string;
    statements: {name:string;account_id:string;statement:string;basis:string;period:string;current:number|null;previous:number|null;prior:number|null;unit:string;receipt:string;source_trace?:Trace}[];
    financial_reports?: Record<string, {
      year: string;
      report_code: string;
      report_name: string;
      basis: "CFS" | "OFS";
      status: string;
      row_count: number;
      rows?: {
        name: string | null;
        account_id: string | null;
        account_detail?: string | null;
        statement: string | null;
        period: string;
        period_label: string;
        report_code: string;
        reported_period?: string | null;
        current: number | null;
        current_cumulative: number | null;
        previous: number | null;
        previous_cumulative: number | null;
        previous_quarter: number | null;
        prior: number | null;
        unit: string;
        receipt?: string | null;
        source_trace?: Trace;
      }[];
    }>;
    financial_indicators?: Record<string, {
      year: string;
      report_code: string;
      report_name: string;
      category_code: string;
      status: string;
      row_count: number;
      rows?: {
        name: string | null;
        code: string | null;
        category: string | null;
        category_code: string;
        value: number | null;
        value_raw: string | number | null;
        unit: null;
        period: string;
        period_label: string;
        report_code: string;
        settlement_date?: string | null;
        source_trace?: Trace;
      }[];
    }>;
  };
  security: Stock;
  status: string;
  collection?: {
    attempts?: number;
    last_attempt_at?: string | null;
    updated_at?: string;
    sources: Record<string, CollectionSource>;
  };
  prices: { rows: Price[]; adjustment: string; source_trace: Trace; received_at?: string } | null;
  detail: {
    facts: Record<string, Fact>;
    financials?: Financial[];
    flows?: {
      date: string;
      foreign: number | null;
      institution: number | null;
      individual: number | null;
      holding: number | null;
      source_trace: Trace;
    }[];
    brokers?: { name: string; buy: number|null; sell: number|null; net?:number|null; as_of?:string; source_trace?:Trace }[];
    brokers_status?: string;
    shareholders?: {
      name: string;
      shares: number;
      ratio: number;
      as_of: string;
    }[];
    company?: Record<string, string>;
    research?: { id: number; tit: string; bnm: string; wdt: string }[];
    source_trace?: Trace;
    source_status?: Record<string, CollectionSource>;
  };
  filings: {
    rows: { title: string; date: string; url: string }[];
    status: string;
    received_at?: string;
  };
};
export type FinancialReportIndex = {
  available_periods?: {year:string;report_code:string;report_name:string}[];
  reports: {
    year: string;
    report_code: string;
    report_name: string;
    basis: "CFS" | "OFS";
    status: string;
    row_count: number;
  }[];
  indicators: {
    year: string;
    report_code: string;
    report_name: string;
    category_code: string;
    status: string;
    row_count: number;
  }[];
};
export type FinancialReportSnapshot = {
  status: string;
  year?: string;
  report_code?: string;
  report_name?: string;
  basis?: "CFS" | "OFS";
  category_code?: string;
  rows: {
    name: string | null;
    account_id?: string | null;
    code?: string | null;
    account_detail?: string | null;
    statement?: string | null;
    value?: number | null;
    value_raw?: string | null;
    current?: number | null;
    current_cumulative?: number | null;
    previous?: number | null;
    previous_cumulative?: number | null;
    previous_quarter?: number | null;
    prior?: number | null;
    unit?: string | null;
    period?: string;
    period_label?: string;
    order?: string | number | null;
    settlement_date?: string | null;
  }[];
};
export const API = "/api/market/v1";
export const defaultColumns: MetricId[] = [
  "price",
  "change",
  "value",
  "cap",
  "foreign",
  "per",
  "pbr",
  "roe",
];
export const defaultQuery: Query = {
  q: "",
  market: "ALL",
  rules: [],
  any_rules: [],
  sort: "value",
  direction: "desc",
};
export const defaults: Preferences = {
  version: 1,
  favorites: [],
  groups: {},
  recent: [],
  views: [],
  columns: defaultColumns,
  widths: {},
  theme: "light",
  large: false,
  textSize: "normal",
  compact: false,
  home: ["ranking", "sectors", "filings"],
  startPage: "market",
};
export const labels: Record<MetricId, string> = {
  price: "현재가",
  change: "등락률",
  volume: "거래량",
  value: "거래대금",
  cap: "시가총액",
  foreign: "외국인 지분율",
  per: "PER",
  pbr: "PBR",
  roe: "ROE",
  dividend: "배당수익률",
  foreign_net: "외국인 순매수",
  institution_net: "기관 순매수",
  revenue: "매출액",
  operating_profit: "영업이익",
};
export const presets: Record<string, MetricId[]> = {
  기본: defaultColumns,
  수급: [
    "price",
    "change",
    "volume",
    "value",
    "foreign",
    "foreign_net",
    "institution_net",
  ],
  실적: ["price", "cap", "revenue", "operating_profit", "roe", "per", "pbr"],
  배당: ["price", "dividend", "per", "pbr", "roe"],
};
export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await marketFetch(API + path, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `자료를 불러오지 못했습니다 (${response.status})`,
    );
  }
  return response.json();
}
export function format(
  value: number | null | undefined,
  metric: string = "price",
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (["value", "cap"].includes(metric))
    return Math.abs(value) >= 1e12
      ? `${(value / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}조`
      : `${(value / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억`;
  if (["change", "foreign", "roe", "dividend"].includes(metric))
    return `${metric === "change" && value > 0 ? "+" : ""}${value.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  if (["per", "pbr"].includes(metric))
    return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "배";
  return `${["foreign_net", "institution_net"].includes(metric) && value > 0 ? "+" : ""}${value.toLocaleString("ko-KR", { maximumFractionDigits: metric === "price" ? 0 : 2 })}`;
}
export function tone(value: number | null | undefined) {
  return value == null || value === 0 ? "" : value > 0 ? "mk-up" : "mk-down";
}
export function restorePreferences(raw: unknown): Preferences {
  if (!raw || typeof raw !== "object") return defaults;
  const p = raw as Partial<Preferences>;
  const codes = (x: unknown) =>
    Array.isArray(x)
      ? x
          .filter((v) => typeof v === "string" && /^[0-9A-Z]{6}$/.test(v))
          .slice(0, 1000)
      : [];
  const cols = Array.isArray(p.columns)
    ? p.columns.filter((k) => k in labels)
    : defaultColumns;
  const widths = (v: unknown) =>
    v && typeof v === "object"
      ? Object.fromEntries(
          Object.entries(v)
            .filter(
              ([k, n]) =>
                k in labels && typeof n === "number" && Number.isFinite(n),
            )
            .map(([k, n]) => [k, Math.max(90, Math.min(240, Number(n)))]),
        )
      : {};
  return {
    ...defaults,
    version: 1,
    favorites: codes(p.favorites),
    recent: codes(p.recent).slice(0, 12),
    columns: [...new Set(cols.length ? cols : defaultColumns)],
    views: Array.isArray(p.views)
      ? p.views
          .filter(
            (v) =>
              v &&
              typeof v.id === "string" &&
              typeof v.name === "string" &&
              v.query &&
              Array.isArray(v.columns),
          )
          .slice(0, 30)
          .map((v) => ({
            ...v,
            name: v.name.slice(0, 50),
            query: restoreQuery(v.query),
            columns: v.columns.filter((k) => k in labels),
            widths: widths(v.widths),
          }))
      : [],
    groups:
      p.groups && typeof p.groups === "object"
        ? Object.fromEntries(
            Object.entries(p.groups)
              .slice(0, 30)
              .map(([k, v]) => [k.slice(0, 30), codes(v)]),
          )
        : {},
    widths: widths(p.widths),
    theme: ["light", "dark", "system"].includes(p.theme || "")
      ? p.theme!
      : defaults.theme,
    large: p.large === true,
    textSize: ["normal", "large", "larger"].includes(p.textSize || "") ? p.textSize : p.large ? "large" : "normal",
    compact: p.compact === true,
    sidebarHidden: p.sidebarHidden === true,
    startPage: p.startPage === "watchlist" ? "watchlist" : "market",
    home: Array.isArray(p.home)
      ? [
          ...new Set(
            p.home.filter((k) => ["ranking", "sectors", "filings"].includes(k)),
          ),
        ]
      : defaults.home,
  };
}
export function restoreQuery(raw: unknown): Query {
  if (!raw || typeof raw !== "object") return defaultQuery;
  const r = raw as Partial<Query>;
  const rules = (items: unknown): Rule[] =>
    Array.isArray(items)
      ? items
          .filter(
            (v) =>
              v &&
              typeof v === "object" &&
              v.metric in labels &&
              ["gte", "lte", "eq"].includes(v.operator) &&
              typeof v.value === "number" &&
              Number.isFinite(v.value),
          )
          .slice(0, 20)
          .map((v) => ({
            metric: v.metric,
            operator: v.operator,
            value: v.value,
          }))
      : [];
  return {
    sector: typeof r.sector === "string" ? r.sector.slice(0, 100) : "",
    q: typeof r.q === "string" ? r.q.slice(0, 100) : "",
    market: ["ALL", "KOSPI", "KOSDAQ"].includes(r.market || "")
      ? r.market!
      : "ALL",
    rules: rules(r.rules),
    any_rules: rules(r.any_rules),
    sort: r.sort && r.sort in labels ? r.sort : "value",
    direction: r.direction === "asc" ? "asc" : "desc",
  };
}
