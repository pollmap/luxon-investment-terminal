"use client";

import {useEffect, useState} from "react";
import {Info, RefreshCw} from "lucide-react";
import Dialog from "./dialog";
import {TouchTrend} from "./touch-trend";
import {request, format} from "./types";

type Kind = "lending" | "credit" | "stock_loan";
type Metric = "balance" | "new" | "repaid" | "ratio";
type Row = {date: string; trade_date: string | null; date_basis?:string} & Record<Metric, number | null>;
type Data = Record<Kind, {status: string; rows: Row[]}>;
const kinds: Record<Kind, string> = {lending: "대차잔고", credit: "신용융자", stock_loan: "신용대주"};
const labels: Record<Metric, string> = {balance: "잔고수량", new: "신규", repaid: "상환", ratio: "잔고비율"};

export default function FinancingPanel({code}: {code: string}) {
  const [kind, setKind] = useState<Kind>("lending");
  const [metric, setMetric] = useState<Metric>("balance");
  const [period, setPeriod] = useState("quarter");
  const [data, setData] = useState<Data | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [help, setHelp] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>, busy = false;
    setData(null); setFailed(false);
    async function load() {
      if (busy || document.hidden) { timer = setTimeout(load, 15000); return; }
      busy = true;
      try {
        const next = await request<Data>(`/securities/${code}/financing?period=${period}`, {signal: abort.signal});
        if (!abort.signal.aborted) {setData(next); setFailed(false);}
      } catch { if (!abort.signal.aborted) setFailed(true); }
      finally { busy = false; if (!abort.signal.aborted) timer = setTimeout(load, 30000); }
    }
    const visible = () => { if (!document.hidden && !busy) { clearTimeout(timer); void load(); } };
    void load(); document.addEventListener("visibilitychange", visible);
    return () => { abort.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [code, period, retry]);
  const section = data?.[kind];
  const rows = section?.rows || [], latest = rows.at(-1);
  const metrics: Metric[] = kind === "lending" ? ["balance", "new", "repaid"] : ["balance", "new", "repaid", "ratio"];
  const selected = metrics.includes(metric) ? metric : "balance";
  const display = (row: Row | undefined, key: Metric) => row?.[key] == null ? "—" : `${format(row[key], key === "ratio" ? "foreign" : "volume")}${key === "ratio" ? "" : "주"}`;
  return <div className="mk-financing">
    <div className="mk-segment mk-activity-period" aria-label="대차·신용 종류">{(Object.keys(kinds) as Kind[]).map(key => <button key={key} aria-pressed={kind === key} onClick={() => setKind(key)}>{kinds[key]}</button>)}</div>
    <div className="mk-section-head"><h3>{kinds[kind]}</h3><button className="mk-help-button" aria-label="대차·신용 설명" onClick={() => setHelp(true)}><Info size={20}/></button></div>
    <div className="mk-segment mk-activity-period" aria-label="대차·신용 기간">{[["month","1개월"],["quarter","3개월"],["year","1년"],["three_years","3년"],["five_years","5년"],["all","전체"]].map(([value,label]) => <button key={value} aria-pressed={period === value} onClick={() => setPeriod(value)}>{label}</button>)}</div>
    {latest && <><p className="mk-caption">{latest.date} · {kind === "lending" || latest?.date_basis === "trade" ? "거래일" : "결제일"} 기준{latest.trade_date && latest.date_basis !== "trade" && kind !== "lending" ? ` (매매일 ${latest.trade_date})` : ""}</p>
      <div className="mk-flow-totals">{metrics.map(key => <button key={key} aria-pressed={selected === key} onClick={() => setMetric(key)}><span>{labels[key]}</span><strong>{display(latest,key)}</strong></button>)}</div>
      <h3 className="mk-trend-title">{labels[selected]} 추이</h3>
      <TouchTrend key={`${kind}-${selected}-${period}`} label={`${kinds[kind]} ${labels[selected]}`} unit={selected === "ratio" ? "%" : "주"} points={rows.map(row => ({date: row.date, value: row[selected]}))}/>
      <details className="mk-activity-table"><summary>일별 {kinds[kind]} 표 보기</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>{kind === "lending" || latest?.date_basis === "trade" ? "거래일" : "결제일"}</th>{metrics.map(key => <th key={key}>{labels[key]} · {key === "ratio" ? "%" : "주"}</th>)}</tr></thead><tbody>{[...rows].reverse().map(row => <tr key={row.date}><th>{row.date}</th>{metrics.map(key => <td key={key}>{format(row[key],key === "ratio" ? "foreign" : "volume")}</td>)}</tr>)}</tbody></table></div></details>
    </>}
    {!rows.length && <p className="mk-empty" role="status">{failed || section?.status === "error" ? "자료를 불러오지 못했습니다." : (section?.status === "credentials_missing" || section?.status === "unavailable") ? "자료 연결을 준비하고 있습니다." : section?.status === "no_data" ? "이 기간에 제공되는 자료가 없습니다." : "자료를 불러오고 있습니다…"}</p>}
    {section?.status === "partial" && <p className="mk-caption" role="status">확보된 기간의 자료를 표시합니다.</p>}
    {(failed || section?.status === "error" || section?.status === "stale" || section?.status === "partial") && <div className="mk-retry-row" role="status">{section?.status === "stale" && <span>최근 조회에 실패해 이전 자료를 표시합니다.</span>}<button className="mk-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={16}/>다시 불러오기</button></div>}
    {help && <Dialog title="대차·신용 이해하기" onClose={() => setHelp(false)}><h3>대차잔고</h3><p>빌린 주식 중 아직 돌려주지 않은 수량입니다. 빌린 주식 전부가 공매도에 사용되는 것은 아닙니다.</p><h3>신용융자</h3><p>증권사에서 돈을 빌려 주식을 산 뒤 아직 갚지 않은 잔고입니다. 신규·상환은 그날의 수량이며, 잔고는 남아 있는 수량입니다.</p><h3>신용대주</h3><p>증권사에서 주식을 빌려 매도한 뒤 아직 상환하지 않은 잔고입니다. 전체 대차잔고나 공매도 순보유잔고와 구분해서 보세요.</p><h3>날짜와 단위</h3><p>토스 대차·신용은 매매 기준일을 표시합니다. 다른 원천의 결제일 자료는 구분해서 표시합니다. 수량은 주, 비율은 %입니다. —는 자료가 없다는 뜻이며 0과 다릅니다.</p></Dialog>}
  </div>;
}
