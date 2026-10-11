"use client";
import {useEffect, useState} from "react";
import {format, request} from "./types";
import {streamUrl} from "./public-transport";

type Depth = {
  book: {level:number; ask:number|null; bid:number|null; ask_quantity:number|null; bid_quantity:number|null}[];
  trades: {time:string; price:number; quantity:number}[];
  book_status:string; trades_status:string; venue:string; gap?:boolean;
};
const message = (status:string) => status === "credentials_missing" ? "호가·체결을 연결하지 못했어요." : status === "no_data" ? "조회되는 내역이 없습니다." : status === "error" ? "자료를 불러오지 못했습니다." : "자료를 불러오는 중…";
// Keep a recent same-symbol snapshot across responsive panel relocation.
// Entries never cross securities, and expire rather than masquerading as live data.
const snapshots = new Map<string,{at:number;data:Depth}>();
const recentSnapshot = (code:string) => {
  const saved=snapshots.get(code);
  return saved && Date.now()-saved.at<30000 ? saved.data : null;
};
const validSnapshot = (data:Depth) => Array.isArray(data?.book) && Array.isArray(data?.trades);
const remember = (code:string,data:Depth) => {
  snapshots.delete(code);snapshots.set(code,{at:Date.now(),data});
  if(snapshots.size>64)snapshots.delete(snapshots.keys().next().value!);
};
export default function DepthPanel({code}:{code:string}) {
  const [data,setData] = useState<Depth|null>(()=>recentSnapshot(code)), [error,setError] = useState(false);
  const [connecting,setConnecting] = useState(true);
  const [tab,setTab] = useState("book"), [retry,setRetry] = useState(0);
  useEffect(()=>{
    let socket:WebSocket|null=null,timer:ReturnType<typeof setTimeout>|null=null,closed=false,epoch="",version=-1,generation=0;
    let abort:AbortController|null=null,lastMessage=Date.now();
    setData(recentSnapshot(code));setError(false);
    const disconnect=()=>{generation++;abort?.abort();if(timer)clearTimeout(timer);if(socket){socket.onclose=null;socket.close();socket=null;}};
    const connect=async()=>{
      if(closed||document.hidden)return;
      disconnect();const attempt=generation;abort=new AbortController();setConnecting(true);
      try{
        const snapshot=await request<Depth>(`/securities/${code}/depth`,{signal:abort.signal});
        if(closed||document.hidden||attempt!==generation)return;
        if(!validSnapshot(snapshot))throw new Error("Invalid depth snapshot");
        remember(code,snapshot);setData(snapshot);setError(false);setConnecting(false);
      }catch{if(!closed&&attempt===generation&&!document.hidden){setError(true);setConnecting(false);}}
      if(closed||document.hidden||attempt!==generation)return;
      const next=new WebSocket(streamUrl());socket=next;lastMessage=Date.now();
      next.onopen=()=>next.send(JSON.stringify({codes:[code],channels:["depth"]}));
      next.onmessage=event=>{if(attempt!==generation)return;lastMessage=Date.now();try{const packet=JSON.parse(event.data);if(packet.type!=="depth"||packet.code!==code||!validSnapshot(packet.data))return;if(packet.epoch!==epoch){epoch=packet.epoch;version=-1;}if(!Number.isFinite(packet.version)||packet.version<=version)return;version=packet.version;remember(code,packet.data);setData(packet.data);setError(false);setConnecting(false);}catch{}};
      next.onclose=()=>{if(!closed&&!document.hidden&&attempt===generation){setError(true);timer=setTimeout(connect,3000);}};
    };
    const visibility=()=>{if(document.hidden)disconnect();else void connect();};
    const watchdog=setInterval(()=>{if(!document.hidden&&socket&&Date.now()-lastMessage>30000)socket.close();},10000);
    void connect();document.addEventListener("visibilitychange",visibility);
    return()=>{closed=true;disconnect();clearInterval(watchdog);document.removeEventListener("visibilitychange",visibility);};
  },[code,retry]);
  const rows = tab === "book" ? data?.book : data?.trades;
  const status = (tab === "book" ? data?.book_status : data?.trades_status) || "pending";
  const levels = [...(data?.book ?? [])].sort((a,b)=>a.level-b.level);
  return <section className="mk-panel mk-depth-panel" aria-label="호가·체결">
    <div className="mk-section-head"><h2>호가·체결</h2><span className="mk-caption">{data?.venue || "KRX"}</span></div>
    <div className="mk-segment" role="tablist" aria-label="호가·체결 선택">
      <button role="tab" aria-selected={tab === "book"} onClick={()=>setTab("book")}>호가 잔량</button>
      <button role="tab" aria-selected={tab === "trades"} onClick={()=>setTab("trades")}>최근 체결</button>
    </div>
    {connecting && data && <p className="mk-caption" role="status">연결 확인 중</p>}
    {(error || status === "stale") && <p role="status">갱신이 잠시 멈췄습니다. <button className="mk-button" onClick={()=>setRetry(v=>v+1)}>다시 불러오기</button></p>}
    {tab==="trades"&&data?.gap&&<p className="mk-caption">연결이 끊겼던 동안의 체결은 포함되지 않았습니다.</p>}
    {!rows?.length ? <p role="status" className="mk-empty">{message(error ? "error" : status)}</p> : tab === "book" ?
      <div className="mk-table-scroll"><table className="mk-depth-table"><caption>가격(원) · 주문 잔량(주)</caption>
        <thead><tr><th>매도 잔량</th><th>호가</th><th>매수 잔량</th></tr></thead>
        <tbody>{levels.slice().reverse().map(row=><tr className="mk-depth-ask" key={`ask-${row.level}`}><td>{format(row.ask_quantity)}</td><th scope="row" className="mk-down"><span className="mk-sr">매도 {row.level}호가 </span>{format(row.ask)}</th><td>—</td></tr>)}{levels.map(row=><tr className="mk-depth-bid" key={`bid-${row.level}`}><td>—</td><th scope="row" className="mk-up"><span className="mk-sr">매수 {row.level}호가 </span>{format(row.bid)}</th><td>{format(row.bid_quantity)}</td></tr>)}</tbody>
      </table></div> : <div className="mk-table-scroll"><table className="mk-depth-table"><caption>체결 가격(원) · 체결량(주)</caption><thead><tr><th>시각</th><th>체결가</th><th>체결량</th></tr></thead><tbody>{data?.trades.map((row,index)=><tr key={`${row.time}-${index}`}><td>{row.time}</td><td>{format(row.price)}</td><td>{format(row.quantity)}</td></tr>)}</tbody></table></div>}
  </section>;
}
