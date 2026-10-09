"use client";
import {useState} from 'react';
import dynamic from 'next/dynamic';
import type {Detail} from './types';
const NewsPanel=dynamic(()=>import('./news-panel'),{loading:()=> <p role="status">뉴스를 불러오는 중이에요.</p>});
const RelatedCompanies=dynamic(()=>import('./market-journal').then(module=>module.RelatedCompanies));
const displayDate=(value:string)=>value.replace(/^(\d{4})(\d{2})(\d{2})$/,'$1.$2.$3');
export default function DisclosuresPanel({code,filings,research}:{code:string;filings:Detail['filings']['rows'];research:NonNullable<Detail['detail']['research']>}){
 const [section,setSection]=useState('filings'),[page,setPage]=useState(0),[query,setQuery]=useState('');
 const rows=filings.filter(row=>row.title.includes(query));
 const last=Math.max(0,Math.ceil(rows.length/20)-1),current=Math.min(page,last);
 return <section className="mk-panel">
  <div className="mk-segment" aria-label="공시·뉴스 분류">{[['filings','공시'],['news','뉴스'],['research','리포트']].map(([key,label])=><button key={key} aria-pressed={section===key} onClick={()=>setSection(key)}>{label}</button>)}</div>
  {section==='filings'?<>
   <div className="mk-section-head"><h2>기업 공시</h2><a href={`https://dart.fss.or.kr/dsab001/main.do?autoSearch=true&textCrpNm=${encodeURIComponent(code)}`} target="_blank" rel="noreferrer">공시 전체 보기 ↗</a></div>
   <input aria-label="공시 제목 검색" placeholder="공시 제목 검색" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/>
   {rows.slice(current*20,current*20+20).map(row=><a className="mk-document" key={row.url} href={row.url} target="_blank" rel="noreferrer"><strong>{row.title}</strong><small>{displayDate(row.date)} ↗</small></a>)}
   {!rows.length&&<p>{query?'검색한 공시가 없어요.':'표시할 공시가 없어요.'}</p>}
   <div className="mk-segment" aria-label="공시 페이지"><button disabled={!current} onClick={()=>setPage(current-1)}>이전</button><span>{current+1} / {last+1}</span><button disabled={current>=last} onClick={()=>setPage(current+1)}>다음</button></div>
  </>:section==='news'?<><NewsPanel code={code}/><RelatedCompanies code={code}/></>:<>
   <h2>증권사 리포트</h2>
   {research.map(row=><a className="mk-document" key={row.id} href={`https://stock.naver.com/research/company/${row.id}`} target="_blank" rel="noreferrer"><strong>{row.tit}</strong><small>{row.bnm} · {displayDate(row.wdt)} ↗</small></a>)}
   {!research.length&&<p>이 종목에 연결된 리포트가 없어요.</p>}
  </>}
 </section>;
}
