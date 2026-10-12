"use client";
import {useEffect, useState} from "react";
import {format, request} from "./types";

type Source = {filing_id?:string; inputs?:Source[]};
export type FinancialAnswer = {
  metric:string; name:string; status:string; value:number|null; unit:string;
  period_start?:string; period_end?:string; reason_label?:string; formula?:string;
  source_trace?:Source|null;
};
export type FinancialQuestionsData = {
  code:string; basis:string; status:string; frequency:string;
  period_start:string|null; period_end:string|null;
  rows:FinancialAnswer[]; comparisons:FinancialAnswer[];
};
const observed=(row:FinancialAnswer)=>row.status==='ready'&&typeof row.value==='number'&&Number.isFinite(row.value);
function filings(source:Source|null|undefined):string[] {
  if(!source)return [];
  return [...(/^\d{14}$/.test(source.filing_id||'')?[source.filing_id!]:[]),...(source.inputs||[]).flatMap(filings)];
}
export function FinancialQuestionsView({data}:{data:FinancialQuestionsData}){
  const available=data.rows.filter(observed);
  const links=Array.from(new Set(available.flatMap(row=>filings(row.source_trace))));
  if(!available.length)return <p className="mk-caption" role="status">같은 기간의 이익과 현금흐름을 비교할 원문 근거가 아직 부족해요.</p>;
  return <div aria-label="이익과 현금흐름 비교">
    <p className="mk-caption">{data.basis==='OFS'?'별도':'연결'} · 회사 전체 금액 · {data.frequency==='ttm'?'최근 12개월':'연간'}{data.period_start&&data.period_end?` · ${data.period_start} ~ ${data.period_end}`:''}</p>
    <div className="mk-table-scroll"><table className="mk-data-table"><caption className="mk-caption">이익과 현금흐름 · 억원</caption><thead><tr><th scope="col">항목</th><th scope="col">금액</th></tr></thead><tbody>{data.rows.map(row=><tr key={row.metric}><th scope="row">{row.name}</th><td>{observed(row)?format(row.value!/100000000,'raw'):<>— <small>{row.reason_label||'확인된 입력 없음'}</small></>}</td></tr>)}</tbody></table></div>
    <div className="mk-table-scroll"><table className="mk-data-table"><caption className="mk-caption">같은 기간 비교 · %</caption><tbody>{data.comparisons.map(row=><tr key={row.metric}><th scope="row">{row.name}</th><td>{observed(row)?`${format(row.value,'raw')}%`:<>— <small>{row.reason_label||'계산할 입력 없음'}</small></>}</td></tr>)}</tbody></table></div>
    <details><summary>계산과 원문 보기</summary>{[...available,...data.comparisons.filter(observed)].map(row=><p key={row.metric}>{row.name}: {row.formula||'공시의 실제 금액'}</p>)}{links.map(id=><p key={id}><a href={`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${id}`} target="_blank" rel="noopener noreferrer">DART 공시 {id}</a></p>)}</details>
  </div>;
}
export default function FinancialQuestions({code,basis}:{code:string;basis:string}){
  const [data,setData]=useState<FinancialQuestionsData|null>(null),[error,setError]=useState(false),[retry,setRetry]=useState(0);
  const selectedBasis=basis==='OFS'?'OFS':'CFS';
  useEffect(()=>{
    const abort=new AbortController();setError(false);setData(null);
    request<FinancialQuestionsData>(`/securities/${code}/analysis/financial-questions?basis=${selectedBasis}`,{signal:abort.signal})
      .then(result=>{if(!abort.signal.aborted){if(result.code!==code||result.basis!==selectedBasis)throw new Error('Response identity mismatch');setData(result);}})
      .catch(()=>{if(!abort.signal.aborted)setError(true);});
    return()=>abort.abort();
  },[code,selectedBasis,retry]);
  if(error)return <p role="alert">이익·현금흐름을 불러오지 못했어요. <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>;
  return data&&data.code===code&&data.basis===selectedBasis?<FinancialQuestionsView data={data}/>:<p className="mk-caption" role="status">이익·현금흐름을 불러오는 중…</p>;
}
