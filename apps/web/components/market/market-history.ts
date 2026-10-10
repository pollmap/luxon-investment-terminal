type MarketHistoryState = {
  marketSelection?: string[];
  marketOffset?: number;
  __marketDialog?: string;
};

/** Pass only our state to the native API; Next owns and copies its router state. */
export function writeMarketHistory(
  mode: "push" | "replace",
  url: string | URL,
  patch: Partial<MarketHistoryState> = {},
) {
  const current = history.state;
  const state: MarketHistoryState = {
    marketSelection: current?.marketSelection,
    marketOffset: current?.marketOffset,
    __marketDialog: current?.__marketDialog,
    ...patch,
  };
  if (mode === "push") history.pushState(state, "", url);
  else history.replaceState(state, "", url);
}

export const detailRouteKeys = [
  "tab", "mode", "range", "kind", "bar", "period", "lineInterval",
  "analysis", "activity", "insight", "insight_years", "insight_date",
  "v_metric", "v_basis", "v_years", "v_multiple", "v_mode", "v_frequency",
  "v_interval", "v_from", "v_to",
];
