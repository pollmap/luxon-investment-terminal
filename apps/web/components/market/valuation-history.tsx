"use client";
import {useEffect, useRef, useState} from "react";
import {format,request} from "./types";
type Row={period:string;price:number|null;reference_price:number|null;metric_value:number|null;price_date:string|null};
type Analysis={valuation:{rows:Row[];mean_multiple:number|null;method:string};ttm:{status:string;rows:{account_id:string;value:number;unit:string;period_end:string}[]}};
export default function ValuationHistory({code}:{code:string}){
 const [metric,setMetric]=useState("EPS"),[basis,setBasis]=useState("CFS"),[years,setYears]=useState(10),[multiple,setMultiple]=useState("15");
 const [data,setData]=useState<Analysis|null>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false),[retry,setRetry]=useState(0);
 const [base,setBase]=useState(""),[growth,setGrowth]=useState("0"),[horizon,setHorizon]=useState(3),[calculation,setCalculation]=useState<{year:number;metric_value:number;reference_price:number}[]>([]),[calculationError,setCalculationError]=useState("");
 const calculationVersion=useRef(0);
 const factor=Number(multiple), valid=multiple.trim()!==""&&Number.isFinite(factor)&&factor>0&&factor<=100;
 useEffect(()=>{if(!valid)return; const abort=new AbortController();setBusy(true);setError("");setData(null);
 const timer=setTimeout(()=>{request<Analysis>(`/securities/${code}/analysis?metric=${metric}&basis=${basis}&years=${years}&multiple=${factor}`,{signal:abort.signal}).then(next=>{if(!abort.signal.aborted)setData(next);}).catch(()=>{if(!abort.signal.aborted)setError("분석 자료를 불러오지 못했습니다.");}).finally(()=>{if(!abort.signal.aborted)setBusy(false);});},200);
 return()=>{clearTimeout(timer);abort.abort();};},[code,metric,basis,years,factor,valid,retry]);
 const rows=data?.valuation.rows||[], max=Math.max(1,...rows.flatMap(r=>[r.price||0,r.reference_price||0]));
 const x=(i:number)=>55+i/Math.max(1,rows.length-1)*680,y=(v:number)=>225-v/max*195;
 const clearScenario=()=>{calculationVersion.current++;setCalculation([]);setCalculationError("");};
 return <section className="mk-panel mk-analysis"><h2>주가와 실적 함께 보기</h2>
 <div className="mk-analysis-controls">
 <label>비교 지표<select aria-label="가치평가 지표" value={metric} onChange={e=>{setMetric(e.target.value);setMultiple(e.target.value==="EPS"?"15":"1");clearScenario();}}><option value="EPS">주당순이익 · EPS</option><option value="BPS">주당순자산 · BPS</option></select></label>
 <label>재무 기준<select aria-label="가치평가 재무 기준" value={basis} onChange={e=>setBasis(e.target.value)}><option value="CFS">연결</option><option value="OFS">별도</option></select></label>
 <label>기간<select aria-label="가치평가 기간" value={years} onChange={e=>setYears(Number(e.target.value))}>{[3,5,10,20].map(n=><option key={n} value={n}>{n}년</option>)}</select></label>
 <label>비교할 배수<input aria-label="이익 비교 배수" type="number" inputMode="decimal" min="0.1" max="100" step="0.1" value={multiple} onChange={e=>{setMultiple(e.target.value);clearScenario();}}/></label>
 </div>
 {!valid&&<p role="alert">배수를 0보다 크고 100 이하인 숫자로 입력해 주세요.</p>}
 {error&&<p role="alert">{error} <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>}
 {busy&&<p role="status">계산 중…</p>}
 <p className="mk-caption">결산월 말 주가 · {metric} × 선택 배수 · 원</p>
 {rows.length>=2?<><div className="mk-analysis-legend"><span>● 주가</span><span style={{color:"var(--mk-accent)"}}>━ 실적 × 배수</span></div>
 <svg viewBox="0 0 800 280" role="img" aria-label="결산월 말 주가와 실적 비교 차트">
 {[0,1,2,3].map(i=><g key={i}><line x1="50" x2="750" y1={y(max*i/3)} y2={y(max*i/3)} stroke="var(--mk-line)"/><text x="52" y={y(max*i/3)-6} fill="var(--mk-muted)" fontSize="15">{format(max*i/3)}</text></g>)}
 {rows.map((r,i)=><g key={r.period}>{(["price","reference_price"] as const).map(k=>{const v=r[k],prev=rows[i-1]?.[k],color=k==="price"?"var(--mk-text)":"var(--mk-accent)";return v===null?null:<g key={k}>{prev!=null&&<line x1={x(i-1)} x2={x(i)} y1={y(prev)} y2={y(v)} stroke={color} strokeWidth="3"/>}<circle cx={x(i)} cy={y(v)} r="4" fill={color}/></g>;})}{(i===0||i===rows.length-1)&&<text x={x(i)} y="260" textAnchor="middle" fill="var(--mk-muted)">{r.period}</text>}</g>)}</svg>
 <details><summary>날짜별 숫자 보기</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>결산 기간</th><th>주가 · 원</th><th>{metric} · 원</th><th>실적 × 배수 · 원</th></tr></thead><tbody>{rows.map(r=><tr key={r.period}><th>{r.period}</th><td>{format(r.price)}</td><td>{format(r.metric_value,"raw")}</td><td>{format(r.reference_price)}</td></tr>)}</tbody></table></div></details>
 <p>확보한 결산 시점의 평균 배수: <strong>{format(data?.valuation.mean_multiple,"per")}</strong></p></>:!busy&&!error&&<p>같은 기준의 실적과 주가가 2개 기간 이상 필요합니다.</p>}
 <details><summary>이 차트 읽는 법</summary><p>주가와 실적에 선택한 배수를 곱한 값을 비교합니다. 과거 평균 배수는 이 회사가 거래됐던 가격의 비교 기준입니다.</p><p>현재 확보한 공시 기준으로 정리한 결산월 말 비교입니다. 당시 발표 전 자료를 이용한 투자 성과로 해석하지 않습니다.</p></details>
 <details><summary>내 가정으로 계산하기</summary><form onSubmit={async e=>{e.preventDefault();clearScenario();const version=calculationVersion.current;try{const result=await request<{rows:typeof calculation}>("/analysis/scenario",{method:"POST",body:JSON.stringify({base:Number(base),growth:Number(growth),multiple:factor,years:horizon})});if(version===calculationVersion.current)setCalculation(result.rows);}catch{if(version===calculationVersion.current)setCalculationError("입력값을 확인하거나 잠시 후 다시 계산해 주세요.");}}}>
 <p>{metric}와 성장률을 직접 입력하면 선택한 배수를 적용합니다.</p><div className="mk-analysis-controls">
 <label>시작 {metric} · 원<input aria-label="계산 시작 지표" required type="number" inputMode="decimal" min="0.01" max="1000000000" step="any" value={base} onChange={e=>{setBase(e.target.value);clearScenario();}}/></label>
 <label>연간 성장률 · %<input aria-label="가정 성장률" required type="number" inputMode="decimal" min="-99.99" max="100" step="any" value={growth} onChange={e=>{setGrowth(e.target.value);clearScenario();}}/></label>
 <label>계산 기간<select value={horizon} onChange={e=>{setHorizon(Number(e.target.value));clearScenario();}}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}년</option>)}</select></label></div>
 <button className="mk-button mk-primary" disabled={!valid}>내 가정으로 계산</button></form>
 {calculationError&&<p role="alert">{calculationError}</p>}{calculation.length>0&&<div aria-live="polite"><p>사용자 가정에 따른 계산 · 배당 제외</p>{calculation.map(r=><p key={r.year}>{r.year}년 후 {metric} {format(r.metric_value,"raw")}원 × {multiple}배 = <strong>{format(r.reference_price)}원</strong></p>)}</div>}
 </details>
 <details><summary>최근 12개월 실적 · TTM</summary>{data?.ttm.rows.length?data.ttm.rows.map(r=><p key={`${r.account_id.replace(/^IS:/, "")}:${r.period_end}`}>{r.account_id.replace(/^IS:/, "")}: {format(r.value,"raw")} {r.unit} · {r.period_end}까지</p>):<p>계산에 필요한 분기 기간 자료가 없습니다. 네 분기의 기간과 연결 기준이 확인되면 합산합니다.</p>}</details>
 </section>;
}
