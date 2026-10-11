"use client";
import {useEffect,useMemo,useState} from "react";
import {format,tone,request,type Price,type Detail,type FinancialReportIndex,type FinancialReportSnapshot} from './types';
export function PerformanceStrip({code}:{code:string}){
 const [prices,setPrices]=useState<Price[]>([]);
 useEffect(()=>{const abort=new AbortController();let busy=false;const load=async()=>{if(document.hidden||busy)return;busy=true;try{const result=await request<{rows:Price[]}>(`/securities/${code}/price-window?view=returns`,{signal:abort.signal});if(!abort.signal.aborted)setPrices(result.rows);}catch{/* Keep the last observed return; never invent zero. */}finally{busy=false;}};void load();const timer=setInterval(load,60000);document.addEventListener('visibilitychange',load);return()=>{abort.abort();clearInterval(timer);document.removeEventListener('visibilitychange',load);};},[code]);
 const last=prices.at(-1);if(!last)return null;
 const periods=[['1주',7],['1개월',30],['3개월',90],['1년',365]] as const;
 return <section className="mk-return-strip" aria-label="기간별 주가 수익률">{periods.map(([label,days])=>{const cutoff=new Date(new Date(last.date).getTime()-days*86400000).toISOString().slice(0,10);const first=prices.slice().reverse().find(p=>p.date<=cutoff);const value=first?.close?(last.close/first.close-1)*100:null;return <div key={label} title={first?`${first.date} 종가부터 ${last.date} 종가까지. 배당 제외.`:'가격 이력이 부족합니다.'}><span>{label} 주가 수익률</span><strong className={tone(value)}>{format(value,'change')}</strong><small>{first?`${first.date} 기준`:'이력 부족'}</small></div>;})}</section>;
}
export function FlowVisual({flows}:{flows:NonNullable<Detail['detail']['flows']>}){
 if(!flows.length)return null;
 const rows=flows.slice(0,5).reverse(),max=Math.max(1,...rows.flatMap(r=>[Math.abs(r.foreign??0),Math.abs(r.institution??0),Math.abs(r.individual??0)]));
 return <div className="mk-flow-visual"><div className="mk-flow-legend"><span>■ 개인</span><span>■ 기관</span><span>■ 외국인</span></div><div className="mk-flow-bars">{rows.map(r=><div key={r.date}><div className="mk-flow-column">{(['individual','institution','foreign'] as const).map((key,i)=><i key={key} title={`${r.date} ${key} ${format(r[key])}주`} style={{left:`${i*28+8}%`,height:`${Math.abs(r[key]??0)/max*46}%`,bottom:(r[key]??0)>=0?'50%':`${50-Math.abs(r[key]??0)/max*46}%`,background:['var(--mk-muted)','var(--mk-accent)','var(--mk-up)'][i]}}/>)}</div><span>{r.date.slice(5)}</span></div>)}</div><p className="mk-caption">순매수 + · 순매도 − · 주</p></div>;
}
const reportOrder:Record<string,number>={"11011":4,"11014":3,"11012":2,"11013":1};
const statementNames:Record<string,string>={BS:"재무상태표",IS:"손익계산서",CIS:"포괄손익계산서",CF:"현금흐름표",SCE:"자본변동표"};
const indicatorCategories:[string,string][]=[["M210000","수익성"],["M220000","안정성"],["M230000","성장성"],["M240000","활동성"]];
type PeriodChoice={year:string;report_code:string;report_name:string;status:string;row_count:number};
const reportIdentity=(row:{year:string;report_code:string})=>`${row.year}:${row.report_code}`;
const sortReports=(rows:PeriodChoice[])=>rows.slice().sort((a,b)=>Number(b.year)-Number(a.year)||(reportOrder[b.report_code]||0)-(reportOrder[a.report_code]||0));
const numberText=(value:number|null|undefined,divisor=1)=>value==null||!Number.isFinite(value)?"—":(value/divisor).toLocaleString("ko-KR",{maximumFractionDigits:1});
function indicatorUnit(name:string){if(/회전율|배율|배수/.test(name))return "배";if(/율|률|비율|ROE|ROA/i.test(name))return "%";return "";}

function PreviousStatements({data}:{data:NonNullable<Detail['official']>}){
 const rows=useMemo(()=>data.statements||[],[data.statements]);const [basis,setBasis]=useState("CFS");
 const periods=useMemo(()=>[...new Set(rows.filter(row=>row.basis===basis).map(row=>row.period))].sort((a,b)=>Number(b)-Number(a)),[rows,basis]);
 const [period,setPeriod]=useState("");
 useEffect(()=>{if(periods.length&&!periods.includes(period))setPeriod(periods[0]);},[period,periods]);
 const periodRows=rows.filter(row=>row.basis===basis&&row.period===period);
 const statements=[...new Set(periodRows.map(row=>row.statement))];const [statement,setStatement]=useState("BS");
 const activeStatement=statements.includes(statement)?statement:statements[0]||"BS";
 const selected=periodRows.filter(row=>row.statement===activeStatement);if(!rows.length)return null;
 const divisor=(selected[0]?.unit||"KRW")==="KRW"?100000000:1;
 return <section className="mk-panel mk-financial-legacy"><div className="mk-section-head"><div><h2>기본 재무 계정</h2><p className="mk-caption">{basis==="CFS"?"연결":"별도"} · {period}년 · {divisor===1?(selected[0]?.unit||"통화 단위"):"억원"}</p></div></div><div className="mk-financial-controls"><div className="mk-segment" aria-label="재무제표 기준"><button aria-pressed={basis==="CFS"} onClick={()=>setBasis("CFS")}>연결</button><button aria-pressed={basis==="OFS"} onClick={()=>setBasis("OFS")}>별도</button></div>{periods.length>1&&<select aria-label="사업연도 선택" value={period} onChange={event=>setPeriod(event.target.value)}>{periods.map(year=><option key={year} value={year}>{year}년</option>)}</select>}</div><div className="mk-segment">{statements.map(id=><button key={id} aria-pressed={activeStatement===id} onClick={()=>setStatement(id)}>{statementNames[id]||id}</button>)}</div><div className="mk-table-scroll mk-financial-table-scroll"><table className="mk-data-table mk-financial"><thead><tr><th>계정</th><th>{period}</th><th>{Number(period)-1}</th><th>{Number(period)-2}</th></tr></thead><tbody>{selected.map((row,index)=><tr key={`${row.account_id}-${index}`}><th>{row.name}</th>{(["current","previous","prior"] as const).map(key=><td key={key}>{numberText(row[key],divisor)}</td>)}</tr>)}</tbody></table></div>{!selected.length&&<p className="mk-muted">이 재무제표에 표시할 계정이 없습니다.</p>}</section>;
}

export function OfficialStatements({code,data}:{code:string;data:NonNullable<Detail['official']>}){
 const [index,setIndex]=useState<FinancialReportIndex|null>(null);
 const [indexError,setIndexError]=useState("");
 const [refresh,setRefresh]=useState(0);
 const [view,setView]=useState<"statements"|"indicators">("statements");
 const [basis,setBasis]=useState<"CFS"|"OFS">("CFS");
 const [category,setCategory]=useState("M210000");
 const [periodKey,setPeriodKey]=useState("");
 const [snapshot,setSnapshot]=useState<FinancialReportSnapshot|null>(null);
 const [snapshotError,setSnapshotError]=useState("");
 const [statement,setStatement]=useState("BS");
 const [periodMode,setPeriodMode]=useState<"current"|"cumulative">("cumulative");
 const [query,setQuery]=useState("");

 useEffect(()=>{
  let active=true;
  let busy=false;
  const abort=new AbortController();
  setIndex(null);setIndexError("");
  const load=async()=>{
   if(busy||document.hidden)return;
   busy=true;
   try{
    const next=await request<FinancialReportIndex>(`/securities/${code}/financials`,{signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});
    if(active){setIndex(next);setIndexError("");}
   }catch{
    if(active)setIndexError("재무 자료를 불러오지 못했습니다.");
   }finally{busy=false;}
  };
  void load();
  const timer=setInterval(()=>void load(),300000);
  document.addEventListener("visibilitychange",load);
  return()=>{active=false;abort.abort();clearInterval(timer);document.removeEventListener("visibilitychange",load);};
 },[code,refresh]);

 const reportChoices=useMemo(()=>{
  const rows:PeriodChoice[]=view==="statements"
   ?index?.reports.filter(row=>row.basis===basis&&row.status==="ready"&&row.row_count>0)||[]
   :index?.indicators.filter(row=>row.category_code===category&&row.status==="ready"&&row.row_count>0)||[];
  if(view==="statements"){
   const indexRows=new Map((index?.reports.filter(row=>row.basis===basis)||[]).map(row=>[reportIdentity(row),row]));
   return sortReports((index?.available_periods||rows).map(row=>({...row,status:indexRows.get(reportIdentity(row))?.status||"pending",row_count:indexRows.get(reportIdentity(row))?.row_count||0})));
  }
  return sortReports(rows);
 },[basis,category,index,view]);

 useEffect(()=>{
  if(reportChoices.length&&!reportChoices.some(row=>reportIdentity(row)===periodKey))
   setPeriodKey(reportIdentity(reportChoices.find(row=>row.status==="ready"&&row.row_count>0)||reportChoices[0]));
 },[periodKey,reportChoices]);

 useEffect(()=>{
  if(view!=="statements"||!index)return;
  const hasConsolidated=index.reports.some(row=>row.basis==="CFS"&&row.status==="ready");
  const hasSeparate=index.reports.some(row=>row.basis==="OFS"&&row.status==="ready");
  if(!hasConsolidated&&hasSeparate&&basis==="CFS")setBasis("OFS");
 },[basis,index,view]);

 const selected=reportChoices.find(row=>reportIdentity(row)===periodKey)||reportChoices[0];
 const selectedYear=selected?.year||"";
 const selectedReportCode=selected?.report_code||"";

 useEffect(()=>{
  if(!selectedYear||!selectedReportCode){setSnapshot(null);setSnapshotError("");return;}
  let active=true;
  let busy=false;
  let settled=false;
  const abort=new AbortController();
  setSnapshot(null);
  setSnapshotError("");
  const load=async()=>{
   if(busy||settled||document.hidden)return;
   busy=true;
   try{
    const params=new URLSearchParams({year:selectedYear,report_code:selectedReportCode});
    if(view==="statements")params.set("basis",basis);
    else params.set("category",category);
    const result=await request<FinancialReportSnapshot>(`/securities/${code}/financials?${params.toString()}`,{signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});
    if(active){
     setSnapshot(result);
     setSnapshotError("");
     if(result.status!=="pending"&&result.status!=="rate_limited")settled=true;
    }
   }catch{
    if(active)setSnapshotError("재무 자료를 불러오지 못했습니다.");
   }finally{busy=false;}
  };
  void load();
  const timer=setInterval(()=>void load(),12000);
  document.addEventListener("visibilitychange",load);
  return()=>{active=false;abort.abort();clearInterval(timer);document.removeEventListener("visibilitychange",load);};
 },[basis,category,code,refresh,selectedReportCode,selectedYear,view]);

 const oldData=data.statements||[];
 const readyRows=snapshot?.status==="ready"?snapshot.rows:[];
 const statementCodes=[...new Set(readyRows.map(row=>row.statement).filter((value):value is string=>Boolean(value)))];
 const activeStatement=statementCodes.includes(statement)?statement:statementCodes.includes("BS")?"BS":statementCodes[0]||"BS";
 const statementRows=readyRows.filter(row=>row.statement===activeStatement).sort((a,b)=>Number(a.order||0)-Number(b.order||0));
 const visibleStatementRows=statementRows.filter(row=>!query||`${row.name||""} ${row.account_detail||""} ${row.account_id||""}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
 const isIncome=["IS","CIS"].includes(activeStatement);
 const annual=selected?.report_code==="11011";
 const quarterToggle=isIncome&&!annual&&selected?.report_code!=="11013";
 const units=[...new Set(statementRows.map(row=>row.unit||"KRW"))];
 const divisor=units.length===1&&units[0]==="KRW"?100000000:1;
 const unitLabel=divisor===100000000?"억원":units.length===1?units[0]:"계정별 통화";
 const columns:{label:string;key:"current"|"current_cumulative"|"previous"|"previous_cumulative"|"previous_quarter"|"prior"}[]=
  activeStatement==="BS"
   ?[{label:annual?"당기":"당기말",key:"current"},{label:annual?"전기":"전기말",key:"previous"},...(annual?[{label:"전전기",key:"prior" as const}]:[])]
   :annual
    ?[{label:"당기",key:"current"},{label:"전기",key:"previous"},{label:"전전기",key:"prior"}]
    :isIncome&&selected?.report_code==="11013"
     ?[{label:"당기 3개월",key:"current"},{label:"전년 동기 3개월",key:"previous_quarter"}]
     :quarterToggle
      ?periodMode==="current"
       ?[{label:"당기 3개월",key:"current"},{label:"전년 동기 3개월",key:"previous_quarter"}]
       :[{label:"당기 누적",key:"current_cumulative"},{label:"전년 누적",key:"previous_cumulative"}]
      :[{label:"당기",key:"current"},{label:"당기 누적",key:"current_cumulative"},{label:"전기",key:"previous"},{label:"전기 누적",key:"previous_cumulative"}];

 if(!oldData.length&&!index&&!indexError)
  return <section className="mk-panel"><div className="mk-financial-empty" role="status">재무 정보를 불러오고 있습니다…</div></section>;

 return <div className="mk-financial-stack">
  <section className="mk-panel mk-financial-explorer">
   <div className="mk-section-head">
    <h2>재무제표 · 주요 지표</h2>
    <button className="mk-financial-refresh" onClick={()=>setRefresh(value=>value+1)}>새로고침</button>
   </div>
   <div className="mk-financial-controls">
    <div className="mk-segment" aria-label="재무 자료 종류">
     <button aria-pressed={view==="statements"} onClick={()=>setView("statements")}>재무제표</button>
     <button aria-pressed={view==="indicators"} onClick={()=>setView("indicators")}>주요 지표</button>
    </div>
    {view==="statements"&&<div className="mk-segment" aria-label="연결 또는 별도">
     <button aria-pressed={basis==="CFS"} onClick={()=>setBasis("CFS")}>연결</button>
     <button aria-pressed={basis==="OFS"} onClick={()=>setBasis("OFS")}>별도</button>
    </div>}
    {view==="indicators"&&<div className="mk-segment" aria-label="주요 지표 종류">
     {indicatorCategories.map(([id,label])=><button key={id} aria-pressed={category===id} onClick={()=>{setCategory(id);setPeriodKey("");}}>{label}</button>)}
    </div>}
    <select aria-label="재무 보고 기간" value={selected?reportIdentity(selected):""} onChange={event=>setPeriodKey(event.target.value)} disabled={!reportChoices.length}>
     {reportChoices.length?reportChoices.map(row=><option key={reportIdentity(row)} value={reportIdentity(row)}>{row.year}년 {row.report_name}{row.status==="ready"?"":row.status==="no_data"?" · 자료 없음":" · 조회"}</option>):<option value="">{indexError?"자료를 불러오지 못했습니다":"자료 준비 중"}</option>}
    </select>
   </div>

   {indexError&&<p className="mk-financial-state" role="status">{indexError}</p>}
   {snapshotError&&<p className="mk-financial-state" role="status">{snapshotError}</p>}

   {view==="statements"&&selected&&snapshot?.status==="ready"&&<>
    <div className="mk-financial-subcontrols">
     <div className="mk-segment" aria-label="재무제표 종류">
      {statementCodes.map(id=><button key={id} aria-pressed={activeStatement===id} onClick={()=>setStatement(id)}>{statementNames[id]||id}</button>)}
     </div>
     {quarterToggle&&<div className="mk-segment" aria-label="분기 또는 누적">
      <button aria-pressed={periodMode==="current"} onClick={()=>setPeriodMode("current")}>3개월</button>
      <button aria-pressed={periodMode==="cumulative"} onClick={()=>setPeriodMode("cumulative")}>누적</button>
     </div>}
     <label className="mk-financial-search"><span className="mk-sr-only">계정 검색</span><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="계정 찾기" /></label>
     <span className="mk-financial-count">{visibleStatementRows.length.toLocaleString("ko-KR")}개 계정 · {unitLabel}</span>
    </div>
    <div className="mk-table-scroll mk-financial-table-scroll">
     <table className="mk-data-table mk-financial">
      <thead><tr><th>계정</th>{columns.map(column=><th key={column.key}>{column.label}</th>)}</tr></thead>
      <tbody>{visibleStatementRows.map((row,index)=>{
       const rowDivisor=units.length===1?divisor:(row.unit||"KRW")==="KRW"?100000000:1;
       return <tr key={`${row.account_id||row.name}-${index}`}>
        <th title={row.account_detail||undefined}>{row.name||row.account_detail||"이름 없는 계정"}{units.length>1&&<small>{row.unit||"KRW"}</small>}</th>
        {columns.map(column=>{const value=row[column.key];return <td key={column.key}>{value==null?"—":numberText(value,rowDivisor)}</td>;})}
       </tr>;
      })}</tbody>
     </table>
     {!visibleStatementRows.length&&<p className="mk-financial-state">검색 결과가 없습니다.</p>}
    </div>
   </>}

   {view==="indicators"&&selected&&snapshot?.status==="ready"&&<div className="mk-table-scroll mk-financial-table-scroll">
    <table className="mk-data-table mk-financial mk-ratio-table">
     <thead><tr><th>지표</th><th>{selected.year}년 {selected.report_name}</th><th>결산일</th></tr></thead>
     <tbody>{readyRows.map((row,index)=>{
      const name=row.name||"이름 없는 지표";
      const unit=indicatorUnit(name);
      const shown=row.value==null?"—":`${numberText(row.value)}${unit}`;
      return <tr key={`${row.code||name}-${index}`}><th title={row.code||undefined}>{name}</th><td>{shown}</td><td>{row.settlement_date||"—"}</td></tr>;
     })}</tbody>
    </table>
    {readyRows.length===0&&<p className="mk-financial-state">이 기간에 표시할 주요 지표가 없습니다.</p>}
   </div>}

   {selected&&snapshot&&snapshot.status!=="ready"&&<div className="mk-financial-empty" role="status">{snapshot.status==="no_data"?"이 기간의 자료가 없습니다.":snapshot.status==="error"?"자료를 불러오지 못했습니다.":"재무 자료를 준비하고 있습니다…"}</div>}
   {!selected&&!indexError&&<div className="mk-financial-empty" role="status">열람 가능한 자료를 준비하고 있습니다. 잠시 후 자동으로 표시됩니다.</div>}
   {!selected&&indexError&&<div className="mk-financial-empty" role="status">{indexError}</div>}
  </section>
  {view==="statements"&&reportChoices.length===0&&oldData.length>0&&<PreviousStatements data={data}/>}
 </div>;
}
