"use client";
import { useEffect, useState } from "react";
import {resolveLineInterval, type LineInterval} from "./intraday-line";

export type DetailSelection = { tab: string; mode: string; range: number; kind: string; bar: string; period: string; analysis: string; activity: string; lineInterval:LineInterval };
const defaults: DetailSelection = {tab:"요약",mode:"intraday",range:252,kind:"line",bar:"day",period:"day",analysis:"valuation",activity:"flows",lineInterval:"1min"};
const choices = {tab:["요약","차트","수급·거래원","공매도","재무·지표","기업·주주","공시·자료"],mode:["intraday","history"],range:[21,63,252,756,1260,2600,100000],kind:["line","candle","area","bar"],bar:["day","week","month"],period:["day","week"],analysis:["valuation","financials","dividends","shareholders","estimates","peers"],activity:["flows","brokers","program","financing"],lineInterval:["1min","5min"]};
export function sanitizeSelection(raw: Record<string, unknown>): DetailSelection {
  const selection = Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => {
    const value = key === "range" ? Number(raw[key]) : raw[key];
    return [key, (choices[key as keyof DetailSelection] as unknown[]).includes(value) ? value : fallback];
  })) as DetailSelection;
  if (selection.tab === "기업·주주" && selection.analysis === "financials") selection.tab = "재무·지표";
  return selection;
}
export function useDetailSelection(code: string, preview: boolean) {
  const [value, setValue] = useState(defaults);
  useEffect(() => {
    let saved: Record<string,unknown> = {};
    try { saved = JSON.parse(localStorage.getItem(`stocks.chart.${code}`) || "{}"); } catch { /* Defaults remain usable. */ }
    const query = new URLSearchParams(location.search);
    const shared = query.get("code") === code ? Object.fromEntries([...query].filter(([key]) => key in defaults)) : {};
    setValue(sanitizeSelection({...saved, ...shared,lineInterval:resolveLineInterval(shared.lineInterval,saved?.lineInterval,matchMedia("(max-width: 767px)").matches)}));
  }, [code]);
  const update = (patch: Partial<DetailSelection>) => {
    const next = sanitizeSelection({...value, ...patch});
    setValue(next);
    try { localStorage.setItem(`stocks.chart.${code}`, JSON.stringify(next)); } catch { /* Saving must not block chart controls. */ }
    if (!preview) {
      const query = new URLSearchParams(location.search);
      if (query.get("code") === code) {
        Object.entries(next).forEach(([key, item]) => query.set(key, String(item)));
        history.replaceState(history.state, "", `${location.pathname}?${query}`);
        window.dispatchEvent(new Event("market-route-change"));
      }
    }
  };
  return [value, update] as const;
}
