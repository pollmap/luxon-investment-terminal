"use client";
import {useState} from "react";
import {useVisibleQuery} from "./use-visible-query";
import {calendarSessions,type CalendarSnapshot} from "./market-calendar";

export type MarketContext={indicators?:{rows?:{code:string;price:number|null;unit:string}[]};exchange_rate?:{rate?:number;unit?:string;status?:string;as_of?:string};calendars?:Record<string,CalendarSnapshot>};

export function usePublicData<T>(path:string,initial:T){
  const result=useVisibleQuery<T>(path,60000);
  return {...result,data:result.data??initial};
}

export function ContextDateControls({onApply}:{onApply:(query:string)=>void}){
  return <form className="mk-toolbar" onReset={()=>onApply('')} onSubmit={e=>{
    e.preventDefault();
    // Read the submitted native date controls, including calendar/autofill changes.
    const fields=new FormData(e.currentTarget);
    const date=String(fields.get('date')||''),at=String(fields.get('at')||'');
    const [base,quote]=String(fields.get('pair')||'USD/KRW').split('/');
    const query=new URLSearchParams({base,quote});
    if(date)query.set('date',date);
    if(at)query.set('date_time',`${at.length===16?at+':00':at}+09:00`);
    onApply(query.toString());
  }}>
    <label>거래 일정 기준일 <input type="date" name="date"/></label>
    <label>환율 시각 · 한국 <input type="datetime-local" name="at"/></label>
    <label>환율 <select name="pair" defaultValue="USD/KRW"><option>USD/KRW</option><option>KRW/USD</option></select></label>
    <button className="mk-button" type="submit">조회</button><button className="mk-button" type="reset">현재로</button>
  </form>;
}

export function CalendarTable({calendars}:{calendars:MarketContext['calendars']}){
  return <div className="mk-table-scroll"><table className="mk-data-table"><thead><tr><th>시장</th><th>기준일 · 현지</th><th>다음 거래일 · 현지</th><th>거래시간 · 한국 시각</th></tr></thead><tbody>{(['KR','US'] as const).map(c=>{
    const snapshot=calendars?.[c],today=snapshot?.calendar?.today;
    const times=calendarSessions(today,c);
    return <tr key={c}><th>{c==='KR'?'국내 · 통합':'미국'}</th><td>{today?.date||'—'}</td><td>{snapshot?.calendar?.nextBusinessDay?.date||'—'}</td><td>{!today?(snapshot?.status==='error'?'불러오지 못했어요':'조회 중'):times.rows.length?times.rows.map(s=><div key={s.key}>{s.label} {s.time}</div>):times.status==='closed'?'휴장':'시간 정보 없음'}</td></tr>;
  })}</tbody></table></div>;
}

export function ListingTable({onSelect}:{onSelect:(code:string)=>void}){
  const [market,setMarket]=useState('KOSPI'),[status,setStatus]=useState('ACTIVE'),[share,setShare]=useState('all'),[query,setQuery]=useState(''),[visible,setVisible]=useState(50);
  const path=`/listings?market=${market}&status=${status}&security_type=STOCK${share==='all'?'':`&common_share=${share}`}`;
  const {data,error,retry}=usePublicData<{rows:{symbol:string;name:string;isCommonShare:boolean;openable:boolean}[];status:string}>(path,{rows:[],status:'pending'});
  const rows=data.rows.filter(r=>r.name.includes(query)||r.symbol.includes(query));
  return <section><div className="mk-toolbar"><label>시장 <select value={market} onChange={e=>setMarket(e.target.value)}><option>KOSPI</option><option>KOSDAQ</option></select></label><label>상장 상태 <select value={status} onChange={e=>setStatus(e.target.value)}><option value="ACTIVE">상장</option><option value="SCHEDULED">상장 예정</option><option value="DELISTED">상장 폐지</option></select></label><label>주식 종류 <select value={share} onChange={e=>setShare(e.target.value)}><option value="all">전체</option><option value="true">보통주</option><option value="false">우선주</option></select></label><input aria-label="상장 목록 검색" placeholder="종목명·코드" value={query} onChange={e=>{setQuery(e.target.value);setVisible(50);}}/></div>
    {error||data.status==='error'?<p role="status">불러오지 못했어요. <button onClick={retry}>다시 시도</button></p>:data.status==='pending'?<p role="status">상장 목록을 가져오는 중이에요.</p>:<><p className="mk-caption">토스 제공 목록 · {rows.length.toLocaleString()}개</p><div className="mk-table-scroll" style={{maxHeight:560}}><table className="mk-data-table"><thead><tr><th>종목</th><th>코드</th><th>주식 종류</th></tr></thead><tbody>{rows.slice(0,visible).map(r=><tr key={r.symbol}><th>{r.openable?<button onClick={()=>onSelect(r.symbol)}>{r.name}</button>:r.name}</th><td>{r.symbol}</td><td>{r.isCommonShare?'보통주':'우선주'}</td></tr>)}</tbody></table></div>{!rows.length&&<p>해당하는 종목이 없어요.</p>}{rows.length>visible&&<button className="mk-button" onClick={()=>setVisible(n=>n+50)}>50개 더 보기</button>}</>}
  </section>;
}
