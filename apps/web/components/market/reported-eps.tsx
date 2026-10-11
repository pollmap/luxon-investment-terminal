"use client";
import {useEffect, useState} from "react";
import {format, request} from "./types";

type EpsRow = {fact_id:string;period_start:string;period_end:string;frequency:string;value:number;published_at:string|null};
type Result = {rows:EpsRow[];status:string;ttm_status?:string;ttm_readiness?:{period_end?:string};reconciled_inputs?:{period_start:string;period_end:string;reported_eps:string;weighted_shares:string;profit:string}[]};

export default function ReportedEps({code,basis="CFS"}:{code:string;basis?:string}) {
  const [frequency,setFrequency]=useState("annual");
  const [data,setData]=useState<Result|null>(null),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
  useEffect(()=>{
    const abort=new AbortController();setData(null);setFailed(false);
    request<Result>(`/securities/${code}/financials/per-share?basis=${basis}&frequency=${frequency}`,{signal:abort.signal})
      .then(result=>{if(!abort.signal.aborted)setData(result);})
      .catch(()=>{if(!abort.signal.aborted)setFailed(true);});
    return()=>abort.abort();
  },[code,basis,frequency,retry]);
  return <div className="mk-reported-eps">
    <div className="mk-segment" aria-label="공시 EPS 기간">{[["annual","연간"],["quarterly","분기"],["cumulative","누적"],["ttm","최근 12개월"]].map(([value,label])=><button key={value} aria-pressed={frequency===value} onClick={()=>setFrequency(value)}>{label}</button>)}</div>
    {failed?<p role="alert">불러오지 못했어요. <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>:!data?<p role="status">공시 실적을 불러오는 중이에요.</p>:!data.rows.length?<p className="mk-empty">이 기간에 확인된 공시 EPS가 없어요.</p>:<div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>실적 기간</th><th>EPS · 원/주</th><th>공시일</th></tr></thead><tbody>{data.rows.map(row=><tr key={row.fact_id}><th>{row.period_start} ~ {row.period_end}</th><td>{format(row.value,"raw")}</td><td>{row.published_at?.slice(0,10)||"—"}</td></tr>)}</tbody></table></div>}
    {frequency==='ttm'&&data&&data.ttm_status!=='ready'&&<p className="mk-caption">{data.ttm_readiness?.period_end?`${data.ttm_readiness.period_end} 기준 · `:''}{data.ttm_status==='share_basis_required'?'이익과 주식 수는 대조됐지만 주식분할 기준 확인이 남아 있어요.':'연속 네 분기의 이익과 주식 수가 모두 확인되어야 계산할 수 있어요.'}</p>}
    {!!data?.reconciled_inputs?.length&&<details><summary>EPS 계산에 사용된 공시 숫자</summary><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>실적 기간</th><th>보통주 귀속이익 · 억원</th><th>가중평균 주식 수 · 주</th><th>공시 EPS · 원</th></tr></thead><tbody>{data.reconciled_inputs.map((r,i)=><tr key={i}><th>{r.period_start} ~ {r.period_end}</th><td>{(Number(r.profit)/1e8).toLocaleString('ko-KR',{maximumFractionDigits:1})}</td><td>{Number(r.weighted_shares).toLocaleString('ko-KR')}</td><td>{Number(r.reported_eps).toLocaleString('ko-KR')}</td></tr>)}</tbody></table></div></details>}
    <p className="mk-caption">{basis==="OFS"?"별도":"연결"} · 보통주 기본 EPS · 원/주</p>
  </div>;
}
