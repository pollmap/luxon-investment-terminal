"use client";

import {useEffect, useState} from "react";
import {Info, RefreshCw} from "lucide-react";
import Dialog from "./dialog";
import {TouchTrend} from "./touch-trend";
import {request, format} from "./types";

type ShortRow = {date: string; volume: number | null; value: number | null; ratio: number | null};
type Section = {status: string; rows: ShortRow[]};
type ShortData = {trades: Section; balance: Section};
const labels = {volume: "수량", value: "금액", ratio: "비중"};

export default function ShortSellingPanel({code}: {code: string}) {
  const [kind, setKind] = useState<"trades" | "balance">("trades");
  const [period, setPeriod] = useState("quarter");
  const [metric, setMetric] = useState<keyof typeof labels>("volume");
  const [data, setData] = useState<ShortData | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [help, setHelp] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>, busy = false;
    setData(null);
    setFailed(false);
    async function load() {
      if (busy || document.hidden) { timer = setTimeout(load, 15000); return; }
      busy = true;
      try {
        const next = await request<ShortData>(`/securities/${code}/short-selling?period=${period}`, {signal: controller.signal});
        if (!controller.signal.aborted) { setData(next); setFailed(false); }
      } catch { if (!controller.signal.aborted) setFailed(true); }
      finally { busy = false; if (!controller.signal.aborted) timer = setTimeout(load, 30000); }
    }
    const visible = () => { if (!document.hidden && !busy) { clearTimeout(timer); void load(); } };
    void load();
    document.addEventListener("visibilitychange", visible);
    return () => { controller.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [code, period, retry]);
  const section = data?.[kind];
  const rows = section?.rows || [];
  const latest = rows.at(-1);
  const title = kind === "trades" ? "공매도 거래" : "공매도 순보유잔고";
  const metricName = metric === "volume" ? (kind === "trades" ? "거래량" : "잔고수량") : metric === "value" ? (kind === "trades" ? "거래대금" : "잔고금액") : (kind === "trades" ? "거래량 비중" : "잔고 비율");
  const units = {volume: "주", value: "억원", ratio: "%"};
  const display = (row: ShortRow | undefined, key: keyof typeof labels) => {
    const value = row?.[key];
    if (value == null) return "—";
    return key === "ratio" ? format(value,"foreign") : key === "value" ? `${format(value / 1e8,"number")}억원` : `${format(value,"volume")}주`;
  };
  return <section className="mk-panel mk-short-selling">
    <div className="mk-section-head"><h2>{title}</h2><button className="mk-help-button" aria-label="공매도 거래와 잔고 설명" onClick={() => setHelp(true)}><Info size={20}/></button></div>
    <div className="mk-segment mk-activity-switch" aria-label="공매도 정보 선택"><button aria-pressed={kind === "trades"} onClick={() => setKind("trades")}>거래</button><button aria-pressed={kind === "balance"} onClick={() => setKind("balance")}>잔고</button></div>
    <div className="mk-segment mk-activity-period" aria-label="공매도 기간">{[["month","1개월"],["quarter","3개월"],["year","1년"],["three_years","3년"],["five_years","5년"],["all","전체"]].map(([value,label]) => <button key={value} aria-pressed={period === value} onClick={() => setPeriod(value)}>{label}</button>)}</div>
    {latest && <><p className="mk-caption">{latest.date} 기준</p><div className="mk-flow-totals">{(Object.keys(labels) as (keyof typeof labels)[]).map(key => <button key={key} aria-pressed={metric === key} onClick={() => setMetric(key)}><span>{key === "ratio" ? kind === "trades" ? "거래량 비중" : "잔고 비율" : labels[key]}</span><strong>{display(latest,key)}</strong></button>)}</div>
      <h3 className="mk-trend-title">{metricName} 추이</h3><TouchTrend key={`${kind}-${metric}-${period}`} label={metricName} unit={units[metric]} points={rows.map(row => ({date:row.date,value:row[metric] == null ? null : row[metric]! / (metric === "value" ? 1e8 : 1)}))}/>
      <details className="mk-activity-table"><summary>일별 {kind === "trades" ? "공매도 거래" : "잔고"} 표 보기</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>날짜</th><th>{kind === "trades" ? "거래량" : "잔고수량"} · 주</th><th>금액 · 억원</th><th>{kind === "trades" ? "거래량 비중" : "잔고 비율"}</th></tr></thead><tbody>{[...rows].reverse().map(row => <tr key={row.date}><th>{row.date}</th><td>{format(row.volume,"volume")}</td><td>{format(row.value == null ? null : row.value / 1e8,"number")}</td><td>{format(row.ratio,"foreign")}</td></tr>)}</tbody></table></div></details>
    </>}
    {!rows.length && <div className="mk-empty" role="status">{failed || section?.status === "error" ? "자료를 불러오지 못했습니다." : section?.status === "no_data" ? "이 기간에 제공되는 자료가 없습니다." : "공매도 자료를 불러오고 있습니다…"}</div>}
    {(failed || section?.status === "error" || section?.status === "stale") && <div className="mk-retry-row" role="status">{!!rows.length && <span>최근 조회에 실패해 이전 자료를 표시합니다.</span>}<button className="mk-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={16}/>다시 불러오기</button></div>}
    {help && <Dialog title="공매도 거래와 잔고" onClose={() => setHelp(false)}><h3>거래량과 거래량 비중</h3><p>그날 공매도로 체결된 수량과 전체 거래량 중 차지하는 비율입니다. 거래량이 0인 날과 자료가 없는 날은 구분합니다.</p><h3>순보유잔고</h3><p>보고 대상 투자자들의 공매도 순보유 수량을 합산한 값입니다. 거래량의 누적 합계가 아닙니다. 보고 시차가 있으므로 숫자에 적힌 기준 날짜를 확인하세요.</p><h3>대차잔고와의 차이</h3><p>주식을 빌린 잔고 전부가 공매도 잔고인 것은 아닙니다. 대차 자료로 공매도 잔고를 대신 계산하지 않습니다.</p></Dialog>}
  </section>;
}
