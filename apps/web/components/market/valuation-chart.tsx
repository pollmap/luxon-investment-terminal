"use client";
import {useEffect, useId, useMemo, useRef, useState} from "react";
import type {ComparisonLine} from "./financial-chart";
import {format, request} from "./types";
import {writeMarketHistory} from "./market-history";
import {comparisonWindow} from "./analysis-periods";
import {actualMultiple,latestValuationPeriods,periodIdentity,selectedReference,valuationChartRows,valuationPeriodChange,type ValuationPeriod} from "./valuation-series";
import {closestObservedIndex} from './chart-selection';
import MarketSectionBoundary from './market-section-boundary';
import {historyNotice,type PriceHistoryCoverage} from './price-history';
import dynamic from "next/dynamic";
const FinancialChart=dynamic(()=>import("./financial-chart"),{loading:()=> <div className="mk-financial-canvas" style={{height:'var(--mk-chart-height, 420px)'}} aria-busy="true"/>});
const ReportedEps=dynamic(()=>import("./reported-eps"),{loading:()=> <p role="status">공시 실적을 여는 중…</p>});
const FinancialQuestions=dynamic(()=>import("./financial-questions"),{loading:()=> <p role="status">이익·현금흐름을 여는 중…</p>});

type Metric={code:string;label:string;group:string;description:string;formula:string;default_multiple:number|null;supports_ttm:boolean;available?:boolean;reason?:string};
type Point={date:string;price:number;metric_value:number|null;reference_price:number|null;historical_reference:number|null;multiple:number|null;period_end:string|null;comparison_eligible:boolean;reason?:string};
type Analysis={share_basis?:{adjustment?:string;per_share_basis_end?:string};history_coverage?:PriceHistoryCoverage;code:string;metric:string;metric_definition:Metric;multiple:number|null;basis:string;frequency:string;series:Point[];periods:ValuationPeriod[];summary:{representative_multiple:number|null;monthly_observations:number};availability:{comparison:boolean;reasons:string[]};ttm:{rows:{account_id:string;name:string;value:number;unit:string;period_end:string}[]}};
type Selection={metric:string;basis:string;years:number;multiple:string;mode:string;frequency:string;interval:string;from:string;to:string;chart:'price'|'multiple'};
const defaults:Selection={metric:"EPS",basis:"auto",years:5,multiple:"15",mode:"as_known",frequency:"auto",interval:"week",from:"",to:"",chart:'price'};

function storedSelection(code:string):Selection {
  let saved:Partial<Selection>={};
  try{saved=JSON.parse(localStorage.getItem(`market:valuation:${code}`)||"{}");}catch{}
  const params=new URLSearchParams(location.search);
  const value=(key:keyof Selection)=>params.get(`v_${key}`)??saved[key]??defaults[key];
  const choice=(key:keyof Selection,options:string[],fallback:string)=>options.includes(String(value(key)))?String(value(key)):fallback;
  const metric=choice("metric",["EPS","EPS_DILUTED","EPS_ADJUSTED","OCF","FCF","FCFE","FFO","AFFO","EBIT","EBITDA","SALES","BPS"],"EPS");
  const factor=Number(params.get("v_multiple")??saved.multiple??(metric==="BPS"?1:metric==="EPS"||metric==="EPS_DILUTED"?15:""));
  return {metric,basis:choice("basis",["auto","CFS","OFS"],"auto"),
    years:[0,3,5,10].includes(Number(value("years")))?Number(value("years")):5,multiple:factor>0&&factor<=100?String(factor):"",
    mode:choice("mode",["as_known","latest"],"as_known"),frequency:choice("frequency",["auto","annual","ttm"],"auto"),interval:choice("interval",["day","week"],"week"),
    from:/^\d{4}-\d{2}-\d{2}$/.test(String(value("from")))?String(value("from")):"",to:/^\d{4}-\d{2}-\d{2}$/.test(String(value("to")))?String(value("to")):"",chart:choice('chart',['price','multiple'],'price') as Selection['chart']};
}

export default function ValuationChart({code}:{code:string}){
  const [selection,setSelection]=useState(defaults),[ready,setReady]=useState(false);
  const [data,setData]=useState<Analysis|null>(null),[loadedQuery,setLoadedQuery]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(false),[retry,setRetry]=useState(0);
  const [cursorDate,setCursorDate]=useState<string|null>(null),[historical,setHistorical]=useState(false),[expanded,setExpanded]=useState(false);
  const [metrics,setMetrics]=useState<Metric[]>([]);
  const [tableOpen,setTableOpen]=useState(false),[tableCount,setTableCount]=useState(200);
  const [periodCount,setPeriodCount]=useState(5);
  const [epsOpen,setEpsOpen]=useState(false);
  const [cashOpen,setCashOpen]=useState(false);
  const [base,setBase]=useState(""),[growth,setGrowth]=useState("0"),[horizon,setHorizon]=useState(3);
  const [calculation,setCalculation]=useState<{year:number;metric_value:number;reference_price:number}[]>([]),[calculationError,setCalculationError]=useState("");
  const version=useRef(0),uid=useId();
  useEffect(()=>{setSelection(storedSelection(code));setReady(true);setCursorDate(null);},[code]);
  useEffect(()=>{const abort=new AbortController();setMetrics([]);request<{metrics:Metric[]}>(`/securities/${code}/analysis/metrics`,{signal:abort.signal}).then(result=>{if(!abort.signal.aborted)setMetrics(result.metrics);}).catch(()=>{});return()=>abort.abort();},[code,retry]);
  const {metric,basis,years,multiple,mode,frequency,interval,from,to,chart='price'}=selection;
  const queryKey=JSON.stringify([code,metric,basis,years,mode,frequency,interval,from,to]);
  const current=loadedQuery===queryKey&&data?.code===code&&data.metric===metric?data:null;
  const priceLabel=data?.code===code&&data.share_basis?.adjustment==="split"?"분할 기준 주가":"실제 주가";
  const definition=metrics.find(m=>m.code===metric)||current?.metric_definition;
  const factor=multiple.trim()?Number(multiple):current?.multiple??NaN,valid=Number.isFinite(factor)&&factor>0&&factor<=100;
  const label=definition?.label||metric, shownMultiple=valid?format(factor,"raw"):"—";
  const clearScenario=()=>{version.current++;setCalculation([]);setCalculationError("");};
  const update=(patch:Partial<Selection>)=>{
    if(patch.metric&&patch.metric!==metric){setBase("");setPeriodCount(5);}
    const next={...selection,...patch};setSelection(next);if(Object.keys(patch).some(key=>key!=='chart'))clearScenario();
    try{localStorage.setItem(`market:valuation:${code}`,JSON.stringify(next));}catch{}
    const url=new URL(location.href);
    for(const [key,value] of Object.entries(next)){if(value!=="")url.searchParams.set(`v_${key}`,String(value));else url.searchParams.delete(`v_${key}`);}
    writeMarketHistory("replace",url);
    window.dispatchEvent(new Event("market-route-change"));
  };
  useEffect(()=>{
    if(!ready)return;
    const abort=new AbortController();setBusy(true);setError(false);
    const params=new URLSearchParams({metric,basis,years:String(years),mode,frequency,interval});
    if(from)params.set("start",from);if(to)params.set("end",to);
    request<Analysis>(`/securities/${code}/analysis/valuation?${params}`,{signal:abort.signal}).then(result=>{if(!abort.signal.aborted){setData(result);setLoadedQuery(queryKey);}})
      .catch(()=>{if(!abort.signal.aborted)setError(true);}).finally(()=>{if(!abort.signal.aborted)setBusy(false);});
    return()=>abort.abort();
  },[code,metric,basis,years,mode,frequency,interval,from,to,ready,retry,queryKey]);
  // A multiple change reuses the same verified facts; it does not refetch history.
  const rows=useMemo(()=>data?.code===code?data.series.map(p=>current?({...p,reference_price:valid?selectedReference(p,factor):null,multiple:actualMultiple(p)}):({...p,comparison_eligible:false,metric_value:null,reference_price:null,historical_reference:null,multiple:null,period_end:null,reason:undefined})):[],[data,current,code,factor,valid]);
  const dateIndex=closestObservedIndex(rows.map(point=>point.date),cursorDate);
  const index=dateIndex??Math.max(0,rows.length-1),active=rows[index];
  const setCursor=(index:number|null)=>setCursorDate(index===null?rows.at(-1)?.date??null:rows[index]?.date??null);
  const hasComparison=rows.some(p=>p.reference_price!==null);
  const comparisonRange=comparisonWindow(rows);
  const history=data?.code===code?data.history_coverage:undefined,historyMessage=historyNotice(history);
  const hasNonPositiveMetric=rows.some(p=>p.reason==="non_positive_metric");
  const basisEnd=current?.share_basis?.per_share_basis_end;
    const comparisonBasisEndsEarly=Boolean(basisEnd&&rows.at(-1)?.date&&rows.at(-1)!.date>basisEnd);
  const latestComparison=rows.reduce((last,p,i)=>p.reference_price!==null?i:last,-1);
  useEffect(()=>{
    // Pin the actual price date independently of a metric's comparison range.
    // Missing EPS/OCF must not move the user's selection to an older report.
    if(cursorDate===null&&rows.length)setCursorDate(rows.at(-1)!.date);
  },[cursorDate,rows]);
  const periods=useMemo(()=>latestValuationPeriods(current?.periods??[]),[current]);
  const chartRows=useMemo(()=>valuationChartRows(rows,chart),[rows,chart]);
  const representative=current&&current.summary.monthly_observations>=24&&typeof current.summary.representative_multiple==='number'&&Number.isFinite(current.summary.representative_multiple)&&current.summary.representative_multiple>0?current.summary.representative_multiple:null;
  const hasActualMultiple=rows.some(p=>p.multiple!==null);
  const comparisons=useMemo<ComparisonLine[]>(()=>chart==='multiple'?[
    ...(valid?[{values:rows.map(p=>({time:p.date,value:p.multiple!==null?factor:null})),color:'--mk-comparison',dashed:true}]:[]),
    ...(historical&&representative!==null?[{values:rows.map(p=>({time:p.date,value:p.multiple!==null?representative:null})),color:'--mk-amber',dashed:true}]:[])
  ]:[
    {values:rows.map(p=>({time:p.date,value:p.reference_price})),color:'--mk-comparison',area:true},
    ...(historical&&representative!==null?[{values:rows.map(p=>({time:p.date,value:p.comparison_eligible===true&&p.multiple!==null&&typeof p.historical_reference==='number'&&Number.isFinite(p.historical_reference)?p.historical_reference:null})),color:'--mk-amber',dashed:true}]:[])
  ],[rows,historical,chart,valid,factor,representative]);
  const download=()=>{
    const content=[['날짜',`${priceLabel}(원)`,`${label}(원/주)`,'선택 비교 배수','비교값(원)','당시 실제 배수'],...rows.map(p=>[p.date,p.price,p.metric_value??'',valid?factor:'',p.reference_price??'',p.multiple??''])];
    const csv='\uFEFF'+content.map(row=>row.map(value=>'"'+String(value).replaceAll('"','""')+'"').join(',')).join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));
    const link=document.createElement('a');link.href=url;link.download=`${code}-${metric}-${current?.frequency||'annual'}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  return <section className="mk-panel mk-analysis" aria-labelledby={uid} aria-busy={busy}>
    <div className="mk-section-head"><div><h2 id={uid}>실적과 주가</h2><span className="mk-caption">{current?`${current.basis==="OFS"?"별도":"연결"} · ${current.frequency==="ttm"?"최근 12개월":"연간 실적"} · ${chart==='multiple'?'배':'원'}`:chart==='multiple'?'배':'원'}</span></div><button className="mk-button" aria-expanded={expanded} onClick={()=>setExpanded(v=>!v)}>{expanded?"기본 크기":"크게 보기"}</button></div>
    <div className="mk-segment" aria-label="실적 차트 보기"><button aria-pressed={chart==='price'} onClick={()=>update({chart:'price'})}>실적·주가</button><button aria-pressed={chart==='multiple'} onClick={()=>update({chart:'multiple'})}>배수 흐름</button></div>
    <div className="mk-valuation-primary"><div className="mk-segment" aria-label="실적 차트 기간">{[3,5,10,0].map(n=><button key={n} aria-pressed={years===n&&!from&&!to} onClick={()=>update({years:n,from:"",to:""})}>{n?`${n}년`:"전체"}</button>)}</div>
      <label>지표<select aria-label="가치평가 지표" value={metric} onChange={e=>{const next=metrics.find(m=>m.code===e.target.value);update({metric:e.target.value,multiple:next?.default_multiple!=null?String(next.default_multiple):"",frequency:next?.supports_ttm===false?"annual":frequency});}}>{!metrics.length&&<option value={metric}>{label}</option>}{Array.from(new Set(metrics.map(m=>m.group))).map(group=><optgroup key={group} label={group}>{metrics.filter(m=>m.group===group).map(m=><option key={m.code} value={m.code} disabled={m.available===false}>{m.label}{m.available===false?` · ${m.reason}`:""}</option>)}</optgroup>)}</select></label>
      <label>비교 배수<input aria-label="이익 비교 배수" type="number" inputMode="decimal" min="0.1" max="100" step="0.1" value={multiple} placeholder={current?.multiple!=null?format(current.multiple,"raw"):"배수 입력"} onChange={e=>update({multiple:e.target.value})}/></label></div>
    {!valid&&multiple.trim()!==""&&<p role="alert">배수를 0보다 크고 100 이하인 숫자로 입력해 주세요.</p>}
    {!busy&&!valid&&multiple.trim()===""&&current?.availability.reasons.includes('multiple_required')&&<p role="status" className="mk-caption">비교 배수를 입력해 주세요. 과거 배수 표본이 부족해요.</p>}
    {error&&<p role="alert">분석 자료를 불러오지 못했습니다. <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>}
    {busy&&!rows.length&&<div className="mk-valuation-loading" role="status">주가와 실적을 불러오는 중…</div>}
    {!busy&&!error&&!rows.length&&<div className="mk-empty">선택한 기간의 주가가 없습니다.</div>}
    {!!rows.length&&<><div className="mk-analysis-legend"><span style={{color:"var(--mk-accent)"}}>━ {chart==='multiple'?`당시 실제 배수 · ${label}`:priceLabel}</span>{(chart==='multiple'?hasActualMultiple&&valid:hasComparison)&&<span className="mk-legend-reference" style={{color:"var(--mk-comparison)"}}>{chart==='multiple'?`┄ 선택 비교 배수 ${shownMultiple}배`:`━ ${label} × ${shownMultiple}배`}</span>}{historical&&representative!==null&&<span className="mk-legend-historical">┄ 과거 대표 배수 {format(representative,"raw")}배</span>}</div>
      {comparisonRange&&<p className="mk-caption" aria-label="비교 가능한 실적 구간">{comparisonRange.start===comparisonRange.end?`비교 가능한 날짜 ${comparisonRange.start}`:`비교 가능한 구간 ${comparisonRange.start} ~ ${comparisonRange.end}`}{comparisonRange.gaps?' · 중간 공백 있음':''}</p>}
      {hasNonPositiveMetric&&<p className="mk-caption">0 이하인 실적은 배수 비교선에서 제외돼요.</p>}
      {comparisonBasisEndsEarly&&<p className="mk-caption">주식 수 기준은 {basisEnd}까지 확인돼요.</p>}
      {chart==='multiple'&&!busy&&!hasActualMultiple&&<p role="status" className="mk-caption">선택한 기간에 검증된 실제 배수가 없어요.</p>}
      {busy&&!!rows.length&&<p className="mk-caption" role="status">선택한 실적·기간을 불러오는 중이에요.</p>}
      <div className={`mk-valuation-body ${expanded?"mk-valuation-expanded":""}`}><div className="mk-valuation-plot"><MarketSectionBoundary label="실적 차트"><FinancialChart rows={chartRows} breakGaps={chart==='multiple'} rangeKey={code} comparisons={comparisons} onSelect={setCursor} label={chart==='multiple'?`${label} 실제 배수 흐름 차트 · 배`:`${priceLabel}와 실적 배수 비교 차트 · 원`}/></MarketSectionBoundary><div className="mk-trend-dates"><span>{rows[0].date}</span><span>{rows.at(-1)?.date}</span></div></div>
        {active&&<div className="mk-valuation-reading" aria-label="선택한 날짜의 숫자"><strong>{active.date}</strong><dl><div><dt>{priceLabel}</dt><dd>{format(active.price)}<small>원</small></dd></div><div><dt>{label}</dt><dd>{format(active.metric_value,"raw")}<small>원/주</small></dd></div><div><dt>{shownMultiple}배 비교</dt><dd>{format(active.reference_price)}<small>원</small></dd></div><div><dt>당시 실제 배수</dt><dd>{format(active.multiple,"raw")}<small>배</small></dd></div></dl>{active.period_end&&<span className="mk-caption">{active.period_end}까지 실적</span>}</div>}
      </div><div className="mk-valuation-date-buttons"><button className="mk-button" disabled={index===0} onClick={()=>setCursor(index-1)}>이전 날짜</button><button className="mk-button" disabled={index===rows.length-1} onClick={()=>setCursor(index+1)}>다음 날짜</button><button className="mk-button" onClick={()=>setCursor(null)}>최근</button>{!busy&&active?.reference_price===null&&latestComparison>=0&&<button className="mk-button" onClick={()=>setCursor(latestComparison)}>비교 가능한 최근 실적 보기</button>}</div>
      {!busy&&!error&&current&&!hasComparison&&!hasNonPositiveMetric&&!current.availability.reasons.includes('multiple_required')&&<p className="mk-caption">{current.availability.reasons.includes('adjustment_or_venue_unverified')?"주식분할 기준 확인이 필요해요. 공시 실적은 아래 표에서 볼 수 있어요.":"선택한 기간에 비교할 수 있는 실적이 없어요."}</p>}
    </>}
    {history&&<details className="mk-price-coverage"><summary>가격 자료 범위</summary><p>요청 {history.requested.start??'전체'}{history.requested.end?` ~ ${history.requested.end}`:''}</p><p>표시 {history.displayed.start??'—'} ~ {history.displayed.end??'—'}</p><p>저장된 기간 {history.stored.start??'—'} ~ {history.stored.end??'—'}</p><p>실적 비교 {comparisonRange?`${comparisonRange.start} ~ ${comparisonRange.end}${comparisonRange.gaps?' · 중간 공백 있음':''}`:'확인된 구간 없음'}</p>{historyMessage&&<p>{historyMessage}</p>}</details>}
    <details><summary>비교 기준·직접 기간 선택</summary><div className="mk-analysis-controls">
      <label>재무 기준<select aria-label="가치평가 재무 기준" value={basis} onChange={e=>update({basis:e.target.value})}><option value="auto">연결 우선</option><option value="CFS">연결</option><option value="OFS">별도</option></select></label>
      <label>실적 기간<select value={frequency} onChange={e=>update({frequency:e.target.value})}><option value="auto">최근 12개월 우선</option><option value="annual">연간</option><option value="ttm" disabled={definition?.supports_ttm===false}>최근 12개월</option></select></label>
      <label>가격 간격<select value={interval} onChange={e=>update({interval:e.target.value})}><option value="week">주간 종가</option><option value="day">일별 종가</option></select></label>
      <label>실적 반영<select value={mode} onChange={e=>update({mode:e.target.value})}><option value="as_known">당시 공개된 실적</option><option value="latest">최신 정정 실적</option></select></label>
      <label>시작일<input aria-label="실적 차트 시작일" type="date" value={from} max={to||undefined} onChange={e=>update({from:e.target.value})}/></label><label>종료일<input aria-label="실적 차트 종료일" type="date" value={to} min={from||undefined} onChange={e=>update({to:e.target.value})}/></label>
    </div><label className="mk-valuation-check"><input type="checkbox" checked={historical} disabled={representative===null} onChange={e=>setHistorical(e.target.checked)}/>과거 대표 배수선 {representative!==null?`${format(representative,"raw")}배`:"· 월말 자료 24개 필요"}</label></details>
    <details><summary>{metric} 뜻 보기 · 이 차트 읽는 법</summary><p>{definition?.description} 실적에 선택한 배수를 곱해 주가와 같은 원 단위로 비교합니다.</p><p>예: {metric} 1,000원 × {shownMultiple}배 = {valid?format(1000*factor):"—"}원. 비교선만으로 매수·매도를 결정하지 않습니다.</p><details><summary>계산 방식과 확인 조건</summary><p>{definition?.formula}</p>{metric==="EBITDA"&&<p>주당 EBITDA에 곱하는 배수예요. EV/EBITDA와는 다른 계산이에요.</p>}<p>과거 대표 배수는 양수인 월말 배수의 상·하위 10%를 제외한 평균입니다. 선택 기간을 모두 본 뒤 만든 비교 기준이며, 당시의 투자 성과 계산에는 사용하지 않습니다.</p><p>주식 종류, 공개 시점, 분할 조정 기준이 확인된 실적만 주가선과 연결합니다. 적자·결측은 선을 끊고 연간과 최근 12개월을 섞지 않습니다.</p></details></details>
    <details onToggle={e=>setTableOpen(e.currentTarget.open)}><summary>날짜별 숫자 보기</summary>{tableOpen&&<><button className="mk-button" disabled={!current} onClick={download}>CSV 저장</button><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>날짜</th><th>{priceLabel} · 원</th><th>{metric} · 원/주</th><th>당시 실제 배수</th><th>선택 비교 배수</th><th>{shownMultiple}배 · 원</th></tr></thead><tbody>{rows.slice(-tableCount).map(p=><tr key={p.date}><th>{p.date}</th><td>{format(p.price)}</td><td>{format(p.metric_value,"raw")}</td><td>{format(p.multiple,"raw")}</td><td>{shownMultiple}</td><td>{format(p.reference_price)}</td></tr>)}</tbody></table></div>{rows.length>tableCount&&<button className="mk-button" onClick={()=>setTableCount(n=>n+200)}>이전 숫자 더 보기</button>}</>}</details>
    {!!periods.length&&<details open><summary>{label} 실적 · {periods.length}개 기간</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>기간</th><th>{label} · 원/주</th><th>전년 대비</th></tr></thead><tbody>{periods.slice(0,periodCount).map(p=>{const change=valuationPeriodChange(p,periods,{frequency:current?.frequency??frequency,basis:current?.basis??basis});return <tr key={periodIdentity(p)}><th>{p.period_start?`${p.period_start} ~ `:""}{p.period_end}</th><td>{format(p.value,"raw")}</td><td>{change.label}</td></tr>;})}</tbody></table></div>{periods.length>periodCount&&<button className="mk-button" onClick={()=>setPeriodCount(periods.length)}>전체 실적 보기</button>}</details>}
    <details onToggle={event=>setCashOpen(event.currentTarget.open)}><summary>이익과 현금흐름</summary>{cashOpen&&<MarketSectionBoundary label="이익·현금흐름"><FinancialQuestions key={`${code}-${basis}`} code={code} basis={current?.basis||basis}/></MarketSectionBoundary>}</details>
    {metric==="EPS"&&<details onToggle={event=>setEpsOpen(event.currentTarget.open)}><summary>공시 EPS와 주식 수</summary>{epsOpen&&<ReportedEps key={`${code}-${current?.basis}`} code={code} basis={current?.basis||"CFS"}/>}</details>}
    <details><summary>내 가정으로 계산하기</summary><form onSubmit={async e=>{e.preventDefault();clearScenario();const current=version.current;try{const result=await request<{rows:typeof calculation}>("/analysis/scenario",{method:"POST",body:JSON.stringify({base:Number(base),growth:Number(growth),multiple:factor,years:horizon})});if(current===version.current)setCalculation(result.rows);}catch{if(current===version.current)setCalculationError("입력값을 확인하거나 잠시 후 다시 계산해 주세요.");}}}>
      <div className="mk-analysis-controls"><label>시작 {metric} · 원<input aria-label="계산 시작 지표" required type="number" inputMode="decimal" min="0.01" max="1000000000" step="any" value={base} onChange={e=>{setBase(e.target.value);clearScenario();}}/></label><label>연간 성장률 · %<input aria-label="가정 성장률" required type="number" inputMode="decimal" min="-99.99" max="100" step="any" value={growth} onChange={e=>{setGrowth(e.target.value);clearScenario();}}/></label><label>계산 기간<select value={horizon} onChange={e=>{setHorizon(Number(e.target.value));clearScenario();}}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}년</option>)}</select></label></div><button className="mk-button mk-primary" disabled={!valid}>내 가정으로 계산</button></form>
      {calculationError&&<p role="alert">{calculationError}</p>}{!!calculation.length&&<div aria-live="polite"><p>사용자 가정 · 배당 제외</p>{calculation.map(r=><p key={r.year}>{r.year}년 후 {metric} {format(r.metric_value,"raw")}원 × {shownMultiple}배 = <strong>{format(r.reference_price)}원</strong></p>)}</div>}
    </details><details><summary>최근 12개월 실적 · TTM</summary>{current?.ttm.rows.length?current.ttm.rows.map((r,i)=><p key={`${r.account_id}:${i}`}>{r.name}: {format(r.value,"raw")} {r.unit} · {r.period_end}까지</p>):<p>연속 네 분기의 기간과 기준이 확인된 자료가 필요합니다.</p>}</details>
  </section>;
}
