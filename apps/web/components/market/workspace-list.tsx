"use client";
import {useEffect,useState} from "react";
import {Star} from "lucide-react";
import CompanyLogo from "./company-logo";
import {format,request,tone,type Stock} from "./types";

/** Keep the user's list order while switching companies in the workspace. */
export default function WorkspaceList({rows,code,favorites,onOpen,onFavorite}:{rows:Stock[];code:string;favorites:string[];onOpen:(code:string)=>void;onFavorite:(code:string)=>void}) {
  const [fallback,setFallback]=useState<Stock[]>([]),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
  useEffect(()=>{
    if(rows.length)return;
    const abort=new AbortController(),media=matchMedia('(min-width: 1024px)');
    let started=false;
    const load=()=>{
      if(!media.matches||started)return;started=true;setFailed(false);
      void request<{rows:Stock[]}>('/screener',{method:'POST',body:JSON.stringify({market:'ALL',sort:'value',direction:'desc',limit:20,offset:0}),signal:abort.signal})
        .then(result=>{if(!abort.signal.aborted)setFallback(result.rows);})
        .catch(()=>{if(!abort.signal.aborted)setFailed(true);});
    };
    load();media.addEventListener('change',load);
    return()=>{abort.abort();media.removeEventListener('change',load);};
  },[rows.length,retry]);
  const shown=rows.length?rows:fallback;
  return <aside className="mk-workspace-list" aria-label="종목 작업 목록">
    <div className="mk-section-head"><h2>{rows.length?'보고 있던 목록':'거래대금 상위'}</h2><span className="mk-caption">현재가 · 등락률</span></div>
    <div className="mk-workspace-list-scroll">
      {shown.map(stock=><div key={stock.code} className="mk-workspace-list-row" data-active={stock.code===code}>
        <button className="mk-workspace-stock" aria-current={stock.code===code?'true':undefined} onClick={()=>onOpen(stock.code)}>
          <CompanyLogo url={stock.logo_url} name={stock.name}/><span><strong>{stock.name}</strong><small>{stock.market} · {stock.code}</small></span>
          <span className="mk-workspace-quote"><b>{format(stock.price)}</b><small className={tone(stock.change)}>{format(stock.change,'change')}</small></span>
        </button>
        <button className={`mk-star ${favorites.includes(stock.code)?'is-saved':''}`} aria-label={`${stock.name} 관심종목 ${favorites.includes(stock.code)?'해제':'추가'}`} aria-pressed={favorites.includes(stock.code)} onClick={()=>onFavorite(stock.code)}><Star size={16}/></button>
      </div>)}
      {!shown.length&&<p role="status" className="mk-caption">{failed?'목록을 불러오지 못했어요.':'종목 목록을 불러오는 중…'}{failed&&<button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button>}</p>}
    </div>
  </aside>;
}
