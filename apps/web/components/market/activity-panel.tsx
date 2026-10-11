"use client";

import {useEffect,useState} from "react";
import {Info} from "lucide-react";
import Dialog from "./dialog";
import {format, request, tone, type Detail} from "./types";


import dynamic from "next/dynamic";
const TradingHistory = dynamic(() => import("./trading-history"));
const FinancingPanel = dynamic(() => import("./financing-panel"), {loading: () => <p role="status">대차·신용 화면을 여는 중…</p>});



type Trading={brokers:{name:string;buy:number|null;sell:number|null;net:number|null;as_of?:string}[];brokers_status:string;program_status:string;venue:string;program:{buy_quantity:number|null;sell_quantity:number|null;net_quantity:number|null}|null};
export default function ActivityPanel({detail, code, section="flows"}: {detail: Detail["detail"]; code: string; section?:string; holding?:{value:number|null;date?:string}}) {
  const [brokerMetric, setBrokerMetric] = useState<"buy" | "sell" | "net">("buy");
  const [help, setHelp] = useState(false);
  const [trading,setTrading]=useState<Trading|null>(null),[tradingError,setTradingError]=useState(false);
  useEffect(()=>{
    if(section!=="brokers")return;
    const abort=new AbortController();let busy=false;
    const load=async()=>{if(document.hidden||busy)return;busy=true;try{const result=await request<Trading>(`/securities/${code}/trading`,{signal:abort.signal});if(!abort.signal.aborted){setTrading(result);setTradingError(false);}}catch{if(!abort.signal.aborted)setTradingError(true);}finally{busy=false;}};
    void load();const timer=setInterval(load,5000);document.addEventListener("visibilitychange",load);return()=>{abort.abort();clearInterval(timer);document.removeEventListener("visibilitychange",load);};
  },[code,section]);
  const brokers = (trading?.brokers.length ? trading.brokers : detail.brokers || []).map(row => ({...row, net: row.net ?? (row.buy != null && row.sell != null ? row.buy - row.sell : null)})).sort((a,b) => {
    if (a[brokerMetric] == null) return b[brokerMetric] == null ? a.name.localeCompare(b.name) : 1;
    if (b[brokerMetric] == null) return -1;
    return b[brokerMetric]! - a[brokerMetric]!;
  });
  const maxBroker = Math.max(1, ...brokers.map(row => Math.abs(row[brokerMetric] ?? 0)));
  return <section className="mk-panel mk-activity">
    <div className="mk-section-head"><h2>{section === "flows" ? "투자자별 매매" : section === "brokers" ? "증권사별 거래원" : section === "program" ? "프로그램 매매" : "대차·신용"}</h2><button className="mk-button" aria-label="수급과 거래원 설명" onClick={() => setHelp(true)}><Info size={20}/> 뜻 보기</button></div>
    {["brokers","program"].includes(section)&&(tradingError||trading?.[section==="brokers"?"brokers_status":"program_status"]==="stale")&&<p role="status">갱신이 잠시 멈췄습니다. 마지막 자료를 표시합니다.</p>}
    {section === "flows" ? <TradingHistory key={code+"flows"} code={code}/> : section === "financing" ? <FinancingPanel key={code} code={code}/> : section === "program" ? <TradingHistory key={code+"program"} code={code} kind="program-trades"/> : <>
      <div className="mk-segment mk-activity-period" aria-label="증권사 순위 기준">{([['buy','매수량'],['sell','매도량'],['net','순매수']] as const).map(([key,label]) => <button key={key} aria-pressed={brokerMetric === key} onClick={() => setBrokerMetric(key)}>{label}</button>)}</div>
      {brokers[0]?.as_of && <p className="mk-caption">{brokers[0].as_of.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3")} · 주{brokers.length > 10 ? " · 상위 10개" : ""}</p>}
      <ol className="mk-broker-ranks">{brokers.slice(0,10).map((row,i) => <li key={row.name}><span className="mk-rank-number">{i + 1}</span><span>{row.name}</span><strong className={brokerMetric === "net" ? tone(row.net) : ""}>{format(row[brokerMetric],brokerMetric === "net" ? "foreign_net" : "volume")}<small>주</small></strong><div className="mk-broker-track"><i style={{width:`${Math.abs(row[brokerMetric] ?? 0) / maxBroker * 100}%`,background: brokerMetric === "net" && (row.net ?? 0) < 0 ? "var(--mk-down)" : "var(--mk-accent)"}}/></div></li>)}</ol>
      {!brokers.length && <p className="mk-empty">이 종목의 거래원 자료가 없습니다.</p>}
      <details className="mk-activity-table"><summary>증권사별 전체 표 보기</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>증권사</th><th>매수 · 주</th><th>매도 · 주</th><th>순매수 · 주</th></tr></thead><tbody>{brokers.map(row => <tr key={row.name}><th>{row.name}</th><td>{format(row.buy,"volume")}</td><td>{format(row.sell,"volume")}</td><td className={tone(row.net)}>{format(row.net,"foreign_net")}</td></tr>)}</tbody></table></div></details>
    </>}
    {help && <Dialog title="수급과 거래원" onClose={() => setHelp(false)}><h3>순매수</h3><p>매수한 수량에서 매도한 수량을 뺀 값입니다. +는 순매수, −는 순매도입니다. 선택 기간의 일별 순매수를 합산하며, 값이 빠진 날은 0으로 채우지 않습니다.</p><h3>외국인 보유율</h3><p>외국인이 보유한 주식의 비율입니다. 당일 외국인 순매수와 다른 지표입니다.</p><h3>증권사별 거래</h3><p>주문이 집계된 증권사의 거래량입니다. 해당 증권사 자체의 투자나 실제 투자자의 신원을 뜻하지 않습니다. 매수·매도 양쪽을 확인할 수 없으면 순매수를 계산하지 않습니다.</p></Dialog>}
  </section>;
}
