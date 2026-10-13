"use client";
import {useEffect,useMemo,useState} from "react";
import {format,request,tone,type Financial} from "./types";
import {financialYearChange} from "./financial-change";
import {annualPeriodLabel,earningsPeriodWindow,observedFinancialValue} from "./analysis-periods";

const names=["매출액","영업이익","당기순이익"];
const periodContext=(row:Financial)=>JSON.stringify([row.period_start??row.input_period_start??'',row.period_end??row.input_period_end??'',row.unit,row.input_company_id??'']);
const publication=(row:Financial)=>{const at=Date.parse(row.available_at??row.input_published_at??'');return Number.isFinite(at)?at:-Infinity;};
export default function EarningsOverview({code,financials}:{code:string;financials:Financial[]}){
 const [frequency,setFrequency]=useState('annual'),[count,setCount]=useState(3),[metric,setMetric]=useState('매출액');
 const [official,setOfficial]=useState<Financial[]>([]);
 const [error,setError]=useState(false),[retry,setRetry]=useState(0),[busy,setBusy]=useState(false);
 useEffect(()=>{const abort=new AbortController();setOfficial([]);setError(false);setBusy(true);request<{code?:string;rows:Financial[]}>(`/securities/${code}/financials/timeline`,{signal:abort.signal}).then(r=>{if(r.code&&r.code!==code)throw new Error('Financial identity mismatch');if(!abort.signal.aborted)setOfficial(r.rows);}).catch(()=>{if(!abort.signal.aborted)setError(true);}).finally(()=>{if(!abort.signal.aborted)setBusy(false);});return()=>abort.abort();},[code,retry]);
 const values=useMemo(()=>{
  const result=new Map<string,Financial>();
  for(const f of [...financials,...official]){
   if(f.frequency!==frequency||f.estimate||f.basis!=='CFS'||!names.includes(f.name))continue;
   const period=frequency==='annual'?annualPeriodLabel(f.period):f.period;
   const key=JSON.stringify([period,f.name,f.frequency,f.basis,periodContext(f)]),previous=result.get(key);
   if(!previous||publication(f)>=publication(previous))result.set(key,{...f,period});
  }
  return [...result.values()];
 },[financials,official,frequency]);
 const window=earningsPeriodWindow(values.map(f=>f.period),frequency,count);
 const displayContext=(row:Financial)=>{
  if((row.period_start??row.input_period_start)&&(row.period_end??row.input_period_end))return periodContext(row);
  const compatible=values.filter(candidate=>candidate.period===row.period
   &&candidate.period_verified===true&&!candidate.period_reason
   &&(candidate.period_start??candidate.input_period_start)&&(candidate.period_end??candidate.input_period_end)
   &&candidate.unit===row.unit&&candidate.input_company_id===row.input_company_id);
  const contexts=[...new Set(compatible.map(periodContext))];
  // An unknown period in another account is not a second fiscal period.
  // The cell keeps its own unverified context and cannot produce growth/margins.
  if(contexts.length===1&&!compatible.some(candidate=>candidate.name===row.name&&candidate.value!==row.value))return contexts[0];
  return periodContext(row);
 };
 const columns=window.periods.flatMap(period=>{
  const matching=values.filter(row=>row.period===period),contexts=[...new Set(matching.map(displayContext))];
  if(contexts.length<=1)return [{key:period,period,context:contexts[0],label:period,conflict:false}];
  return contexts.map(context=>{const row=matching.find(value=>periodContext(value)===context)!,start=row.period_start??row.input_period_start,end=row.period_end??row.input_period_end;
   return {key:JSON.stringify([period,context]),period,context,label:`${period} · ${start&&end?`${start} ~ ${end}`:'기간 미확인'}`,conflict:true};});
 }),periods=columns.map(column=>column.key),columnLabels=new Map(columns.map(column=>[column.key,column.label]));
 const lookup=(key:string,name:string)=>{const column=columns.find(value=>value.key===key);return column?values.filter(row=>row.period===column.period&&row.name===name&&(column.context===undefined||displayContext(row)===column.context)).sort((a,b)=>Number(b.period_verified===true)-Number(a.period_verified===true)||publication(b)-publication(a))[0]:undefined;};
 const growth=(key:string,name:string)=>columns.find(column=>column.key===key)?.conflict?{percent:null,label:'기간 다름'}:financialYearChange(lookup(key,name),values);
 const series=periods.map(p=>lookup(p,metric)),amounts=series.map(r=>observedFinancialValue(r?.value));
 const max=Math.max(1,...amounts.filter((value):value is number=>value!==null).map(Math.abs));
 if(!periods.length)return <section className="mk-panel mk-earnings"><h2>실적의 흐름</h2><p role={error?'alert':'status'} className="mk-caption">{error?'실적을 불러오지 못했어요.':busy?'실적을 불러오는 중이에요.':'확인된 실적 자료가 없어요.'}{error&&<button className="mk-button" onClick={()=>setRetry(value=>value+1)}>다시 시도</button>}</p></section>;
 return <section className="mk-panel mk-earnings"><div className="mk-section-head"><h2>실적의 흐름</h2><div className="mk-segment"><button aria-pressed={frequency==='annual'} onClick={()=>setFrequency('annual')}>연간</button><button aria-pressed={frequency==='quarter'} onClick={()=>setFrequency('quarter')}>분기</button></div></div>
  <div className="mk-chart-controls"><div className="mk-segment" aria-label="실적 표 기간">{[3,5,10,0].map(n=><button key={n} aria-pressed={count===n} onClick={()=>setCount(n)}>{n?(window.annualYears?`최근 ${n}년`:`${n}개 ${frequency==='quarter'?'분기':'기간'}`):'전체'}</button>)}</div><div className="mk-segment">{names.map(n=><button key={n} aria-pressed={metric===n} onClick={()=>setMetric(n)}>{n}</button>)}</div></div>
  <p className="mk-caption">연결 · 실제 실적 · {series.find(Boolean)?.unit||'단위 미확인'}{window.anchor?` · ${window.anchor} 회계연도까지`:''} · 증감률은 전년 동기 대비</p>
  {error&&<p role="alert" className="mk-caption">추가 실적을 불러오지 못했어요. 확인한 자료를 표시해요. <button className="mk-button" onClick={()=>setRetry(value=>value+1)}>다시 시도</button></p>}
  <div className="mk-table-scroll"><div className="mk-earnings-bars" style={{minWidth:Math.max(420,periods.length*90)}}>{amounts.map((value,i)=>{const change=growth(periods[i],metric);return <div key={periods[i]}><strong>{value===null?'자료 없음':format(value)}</strong><span className={tone(change.percent)}>{change.label}</span><div className="mk-earnings-track">{value!==null&&<i style={{height:`${Math.abs(value)/max*100}%`,background:value<0?'var(--mk-down)':'var(--mk-accent)'}}/>}</div><span>{columnLabels.get(periods[i])}</span></div>;})}</div></div>
  <div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th scope="col">항목</th>{periods.map(p=><th scope="col" key={p}>{columnLabels.get(p)}</th>)}</tr></thead><tbody>{names.flatMap(name=>[<tr key={name}><th scope="row">{name} · {values.find(f=>f.name===name)?.unit}</th>{periods.map(p=>{const value=observedFinancialValue(lookup(p,name)?.value);return <td key={p} aria-label={value===null?`${columnLabels.get(p)} ${name} 자료 없음`:undefined}>{format(value)}</td>;})}</tr>,<tr key={name+'change'}><th scope="row">{name} 증감률</th>{periods.map(p=>{const change=growth(p,name);return <td key={p} className={tone(change.percent)}>{change.label}</td>;})}</tr>])}<tr><th scope="row">영업이익률</th>{periods.map(p=>{const revenue=lookup(p,'매출액'),income=lookup(p,'영업이익');const value=revenue?.value&&revenue.value>0&&income?.value!=null&&Number.isFinite(income.value)&&revenue.unit===income.unit&&revenue.period_verified===true&&income.period_verified===true&&!revenue.period_reason&&!income.period_reason?income.value/revenue.value*100:null;return <td key={p}>{value==null?'—':`${value.toFixed(1)}%`}</td>;})}</tr></tbody></table></div>
 </section>;
}
