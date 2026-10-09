"use client";
import {useEffect,useMemo,useState} from "react";
import {TouchTrend} from "./touch-trend";
import {format,request,tone} from "./types";

type Row={date:string}&Record<string,number|string|null>;
type Data={rows:Row[];columns:{key:string;label:string;unit:string}[];venue:string;status:string;available_start:string|null;available_end:string|null;history_complete:boolean};
const metrics=[["netBuyVolume","순매수"],["buyVolume","매수"],["sellVolume","매도"]];

function trendPoints(rows:Row[],key:string|undefined,cumulative:boolean){
 let total:number|null=0;
 const result:{date:string;value:number|null}[]=[];
 for(const row of rows){
  const value=key&&typeof row[key]==="number"?row[key] as number:null;
  total=value===null||total===null?null:total+value;
  result.push({date:row.date,value:cumulative?total:value});
 }
 return result;
}

export default function TradingHistory({code,kind="investor-trading"}:{code:string;kind?:string}){
 const [data,setData]=useState<Data|null>(null),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
 const [metric,setMetric]=useState("netBuyVolume"),[period,setPeriod]=useState(90),[start,setStart]=useState(""),[end,setEnd]=useState("");
 const [group,setGroup]=useState("all"),[selected,setSelected]=useState(kind==="program-trades"?"total":"foreigner"),[cumulative,setCumulative]=useState(false),[page,setPage]=useState(0);
 useEffect(()=>{
  const abort=new AbortController();let busy=false;setData(null);setFailed(false);
  const load=async()=>{if(busy||document.hidden)return;busy=true;try{const result=await request<Data>(`/securities/${code}/trading/history?kind=${kind}&metric=${metric}`,{signal:abort.signal});if(!abort.signal.aborted){setData(result);setFailed(false);}}catch{if(!abort.signal.aborted)setFailed(true);}finally{busy=false;}};
  void load();const timer=setInterval(load,30000);document.addEventListener("visibilitychange",load);
  return()=>{abort.abort();clearInterval(timer);document.removeEventListener("visibilitychange",load);};
 },[code,kind,metric,retry]);
 const rows=useMemo(()=>{
  const until=end||data?.available_end||"";
  const from=start||(period&&until?new Date(Date.parse(until+"T00:00:00Z")-period*86400000).toISOString().slice(0,10):"");
  return (data?.rows||[]).filter(row=>(!from||row.date>=from)&&(!until||row.date<=until));
 },[data,start,end,period]);
 const columns=(data?.columns||[]).filter((_,i)=>kind==="program-trades"||(group==="institutions"?i>=4:i<4));
 const active=columns.find(c=>c.key===selected)||columns[0];
 const activeKey=active?.key;
 const points=trendPoints(rows,activeKey,cumulative);
 const ordered=rows.slice().reverse(),lastPage=Math.max(0,Math.ceil(rows.length/50)-1),current=Math.min(page,lastPage);
 const shown=ordered.slice(current*50,current*50+50),label=metrics.find(m=>m[0]===metric)?.[1];
 const changePeriod=(days:number)=>{setPeriod(days);setStart("");setEnd("");setPage(0);};
 const exportCsv=()=>{
  const body=[['날짜',...columns.map(c=>c.label+' '+label+'(주)')],...ordered.map(row=>[row.date,...columns.map(c=>row[c.key]??'')])].map(row=>row.join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob(['\ufeff'+body],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`${code}-${kind}-${metric}.csv`;a.click();URL.revokeObjectURL(url);
 };
 return <div>
  <div className="mk-chart-controls"><div className="mk-segment" aria-label="수급 기간">{[[30,"1개월"],[90,"3개월"],[365,"1년"],[1095,"3년"],[1826,"5년"],[0,"전체"]].map(([n,label])=><button key={n} aria-pressed={period===n&&!start&&!end} onClick={()=>changePeriod(Number(n))}>{label}</button>)}</div><div className="mk-segment" aria-label="매매 수량">{metrics.map(([key,label])=><button key={key} aria-pressed={metric===key} onClick={()=>{setMetric(key);setPage(0);}}>{label}</button>)}</div></div>
  <div className="mk-chart-controls"><label>시작일 <input aria-label="수급 시작일" type="date" value={start} max={end||data?.available_end||undefined} onChange={e=>{setStart(e.target.value);setPeriod(0);setPage(0);}}/></label><label>종료일 <input aria-label="수급 종료일" type="date" value={end} min={start||undefined} onChange={e=>{setEnd(e.target.value);setPage(0);}}/></label>{kind!=="program-trades"&&<div className="mk-segment"><button aria-pressed={group==="all"} onClick={()=>setGroup("all")}>투자자별</button><button aria-pressed={group==="institutions"} onClick={()=>setGroup("institutions")}>기관 상세</button></div>}<label className="mk-check"><input type="checkbox" checked={cumulative} onChange={e=>setCumulative(e.target.checked)}/>선택 기간 누적</label></div>
  {failed&&<p role="alert">불러오지 못했어요. <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>}
  {!data&&!failed?<p role="status">일별 자료를 불러오는 중이에요.</p>:<>
   <p className="mk-caption">{rows.length?`${rows[0].date} ~ ${rows.at(-1)!.date} · ${rows.length.toLocaleString()}거래일`:'선택 기간에 수집된 자료가 없어요.'} · {data?.venue} · 주{data&&!data.history_complete?' · 수집된 기간만 표시':''}</p>
   <div className="mk-segment" aria-label="그래프에 표시할 투자자">{columns.map(c=><button key={c.key} aria-pressed={active?.key===c.key} onClick={()=>setSelected(c.key)}>{c.label}</button>)}</div>
   {!!rows.length&&<TouchTrend key={`${active?.key}-${metric}-${cumulative}-${start}-${end}-${period}`} points={points} label={`${active?.label} ${cumulative?'누적 ':''}${label}`} unit="주" signed={metric==="netBuyVolume"}/>}
   <div className="mk-section-head"><h3>일별 {kind==="program-trades"?'프로그램 매매':'투자자 수급'}</h3><button className="mk-button" onClick={exportCsv} disabled={!rows.length}>CSV 저장</button></div>
   <div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>날짜</th>{columns.map(c=><th key={c.key}>{c.label} · {label}</th>)}{kind!=="program-trades"&&group==="all"&&<th>외국인 보유율</th>}</tr></thead><tbody>{shown.map(row=><tr key={row.date}><th>{row.date}</th>{columns.map(c=><td key={c.key} className={metric==="netBuyVolume"?tone(row[c.key] as number|null):''}>{format(row[c.key] as number|null,metric==="netBuyVolume"?'foreign_net':'volume')}</td>)}{kind!=="program-trades"&&group==="all"&&<td>{format(row.holding as number|null,'foreign')}</td>}</tr>)}</tbody></table></div>
   <div className="mk-segment" aria-label="수급 표 페이지"><button disabled={current===0} onClick={()=>setPage(current-1)}>이전 50일</button><span>{current+1} / {lastPage+1}</span><button disabled={current>=lastPage} onClick={()=>setPage(current+1)}>다음 50일</button></div>
  </>}
 </div>;
}
