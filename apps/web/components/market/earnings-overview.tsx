"use client";
import {useEffect,useMemo,useState} from "react";
import {format,request,tone,type Financial} from "./types";
import {earningsChange} from "./earnings-change";

const names=["매출액","영업이익","당기순이익"];
export default function EarningsOverview({code,financials}:{code:string;financials:Financial[]}){
 const [frequency,setFrequency]=useState('annual'),[count,setCount]=useState(3),[metric,setMetric]=useState('매출액');
 const [official,setOfficial]=useState<Financial[]>([]);
 useEffect(()=>{const abort=new AbortController();setOfficial([]);request<{rows:Financial[]}>(`/securities/${code}/financials/timeline`,{signal:abort.signal}).then(r=>{if(!abort.signal.aborted)setOfficial(r.rows);}).catch(()=>{});return()=>abort.abort();},[code]);
 const values=useMemo(()=>{
  const result=new Map<string,Financial>();
  for(const f of [...financials,...official]){
   if(f.frequency!==frequency||f.estimate||f.basis!=='CFS'||!names.includes(f.name))continue;
   const period=frequency==='annual'?f.period.slice(0,4):f.period;
   result.set(`${period}:${f.name}`,{...f,period});
  }
  return [...result.values()];
 },[financials,official,frequency]);
 const allPeriods=[...new Set(values.map(f=>f.period))].sort(),periods=count?allPeriods.slice(-count):allPeriods;
 const lookup=(period:string,name:string)=>values.find(f=>f.period===period&&f.name===name);
 const growth=(period:string,name:string)=>{
  const row=lookup(period,name),previous=lookup(String(Number(period.slice(0,4))-1)+period.slice(4),name);
  return earningsChange(row?.value,row?.unit===previous?.unit?previous?.value:null);
 };
 const series=periods.map(p=>lookup(p,metric)),max=Math.max(1,...series.map(r=>Math.abs(r?.value??0)));
 if(!periods.length)return null;
 return <section className="mk-panel mk-earnings"><div className="mk-section-head"><h2>실적의 흐름</h2><div className="mk-segment"><button aria-pressed={frequency==='annual'} onClick={()=>setFrequency('annual')}>연간</button><button aria-pressed={frequency==='quarter'} onClick={()=>setFrequency('quarter')}>분기</button></div></div>
  <div className="mk-chart-controls"><div className="mk-segment" aria-label="실적 표 기간">{[3,5,10,0].map(n=><button key={n} aria-pressed={count===n} onClick={()=>setCount(n)}>{n?`최근 ${n}${frequency==='annual'?'년':'분기'}`:'전체'}</button>)}</div><div className="mk-segment">{names.map(n=><button key={n} aria-pressed={metric===n} onClick={()=>setMetric(n)}>{n}</button>)}</div></div>
  <p className="mk-caption">연결 · 실제 실적 · {series.find(Boolean)?.unit||'억원'} · 증감률은 전년 동기 대비</p>
  <div className="mk-table-scroll"><div className="mk-earnings-bars" style={{minWidth:Math.max(420,periods.length*90)}}>{series.map((row,i)=>{const change=growth(periods[i],metric);return <div key={periods[i]}><strong>{format(row?.value)}</strong><span className={tone(change.percent)}>{change.label}</span><div className="mk-earnings-track">{row?.value!=null&&<i style={{height:`${Math.max(1,Math.abs(row.value)/max*100)}%`,background:row.value<0?'var(--mk-down)':'var(--mk-accent)'}}/>}</div><span>{periods[i]}</span></div>;})}</div></div>
  <div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>항목</th>{periods.map(p=><th key={p}>{p}</th>)}</tr></thead><tbody>{names.flatMap(name=>[<tr key={name}><th>{name} · {values.find(f=>f.name===name)?.unit}</th>{periods.map(p=><td key={p}>{format(lookup(p,name)?.value)}</td>)}</tr>,<tr key={name+'change'}><th>{name} 증감률</th>{periods.map(p=>{const change=growth(p,name);return <td key={p} className={tone(change.percent)}>{change.label}</td>;})}</tr>])}<tr><th>영업이익률</th>{periods.map(p=>{const revenue=lookup(p,'매출액'),income=lookup(p,'영업이익');const value=revenue?.value&&revenue.value>0&&income?.value!=null&&revenue.unit===income.unit?income.value/revenue.value*100:null;return <td key={p}>{value==null?'—':`${value.toFixed(1)}%`}</td>;})}</tr></tbody></table></div>
 </section>;
}
