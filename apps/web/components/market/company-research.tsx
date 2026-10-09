"use client";
import {useEffect, useState} from "react";
import CompanyLogo from "./company-logo";
import {format, request, tone, type Financial, type Stock} from "./types";

export function EstimatesPanel({financials}: {financials: Financial[]}) {
  const [basis, setBasis] = useState("CFS");
  const source = financials.filter(f => f.estimate && f.frequency === "annual" && f.basis === basis);
  const periods = [...new Set(source.map(f => f.period))].sort();
  const metrics = ["매출액", "영업이익", "당기순이익", "EPS", "BPS", "ROE"];
  return <section className="mk-panel" aria-label="예상실적">
    <div className="mk-section-head"><h2>예상실적</h2><select aria-label="예상실적 재무 기준" value={basis} onChange={e=>setBasis(e.target.value)}><option value="CFS">연결</option><option value="OFS">별도</option></select></div>
    {periods.length ? <><p className="mk-caption">연간 예상실적</p><div className="mk-table-scroll"><table className="mk-data-table mk-financial"><thead><tr><th>항목 · 단위</th>{periods.map(p=><th key={p}>{p} 예상</th>)}</tr></thead><tbody>{metrics.filter(name=>source.some(f=>f.name===name)).map(name=><tr key={name}><th>{name}<small>{source.find(f=>f.name===name)?.unit}</small></th>{periods.map(p=><td key={p} className="mk-estimate">{format(source.find(f=>f.name===name&&f.period===p)?.value,"raw")}</td>)}</tr>)}</tbody></table></div></> : <p className="mk-empty">이 기준으로 확인된 예상실적이 없어요.</p>}
  </section>;
}

type Peers = {sector: string|null; status: string; rows: Stock[]};

type Ttm = {basis:string;rows:{metric:string;name:string;value:number;period_start:string;period_end:string}[]};
export function TtmPanel({code}:{code:string}) {
  const [basis,setBasis]=useState("CFS"),[data,setData]=useState<Ttm|null>(null),[error,setError]=useState(false),[retry,setRetry]=useState(0);
  useEffect(()=>{const abort=new AbortController();setData(null);setError(false);
    request<Ttm>(`/securities/${code}/financials/ttm?basis=${basis}`,{signal:abort.signal}).then(r=>{if(!abort.signal.aborted)setData(r);}).catch(()=>{if(!abort.signal.aborted)setError(true);});return()=>abort.abort();},[code,basis,retry]);
  return <section className="mk-panel" aria-label="최근 12개월 실적"><div className="mk-section-head"><h2>최근 12개월 실적</h2><select aria-label="최근 12개월 재무 기준" value={basis} onChange={e=>setBasis(e.target.value)}><option value="CFS">연결</option><option value="OFS">별도</option></select></div>
    {error?<p role="alert">불러오지 못했어요. <button onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>:!data?<p role="status">실적을 불러오는 중이에요.</p>:!data.rows.length?<p className="mk-empty">연속 네 분기를 확인할 수 있는 자료가 아직 없어요.</p>:<div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>항목</th><th>금액 · 억원</th><th>계산 기간</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.metric}><th>{r.name}</th><td>{format(r.value/100000000,"raw")}</td><td>{r.period_start} ~ {r.period_end}</td></tr>)}</tbody></table></div>}
    <details><summary>최근 12개월 · TTM 뜻 보기</summary><p>회계 기간을 확인한 최근 네 분기의 금액을 합한 값이에요. 누적 실적은 이전 누적 금액을 빼서 각 분기로 나눕니다. 자산·부채 잔액과 주당순이익은 여기에 합산하지 않아요.</p></details>
  </section>;
}

export function PeersPanel({code}: {code: string}) {
  const [data, setData] = useState<Peers|null>(null), [error,setError]=useState(false), [retry,setRetry]=useState(0);
  useEffect(()=>{const abort=new AbortController();setError(false);setData(null);
    request<Peers>(`/securities/${code}/peers`,{signal:abort.signal}).then(r=>{if(!abort.signal.aborted)setData(r);}).catch(()=>{if(!abort.signal.aborted)setError(true);});
    return()=>abort.abort();},[code,retry]);
  return <section className="mk-panel" aria-label="동종 비교"><div className="mk-section-head"><h2>같은 업종의 기업</h2><span className="mk-caption">{data?.sector}</span></div>
    {error ? <p role="alert">불러오지 못했어요. <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p> : !data ? <p role="status">기업을 불러오는 중이에요.</p> : data.status!=="ready" ? <p className="mk-empty">연결된 같은 업종 기업이 없어요.</p> : <><p className="mk-caption">시가총액 순</p><div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>종목</th><th>현재가 · 원</th><th>등락률</th><th>시가총액</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.code} aria-current={r.code===code?"true":undefined}><th><a className="mk-peer-link" href={`?code=${r.code}&tab=${encodeURIComponent("기업·주주")}&analysis=peers`}><CompanyLogo url={r.logo_url} name={r.name}/><span>{r.name}<small>{r.market} · {r.code}{r.code===code?" · 현재 종목":""}</small></span></a></th><td>{format(r.price)}</td><td className={tone(r.change)}>{format(r.change,"change")}</td><td>{format(r.cap,"cap")}</td></tr>)}</tbody></table></div></>}
  </section>;
}
