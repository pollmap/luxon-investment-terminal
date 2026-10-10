"use client";
import {useEffect,useRef,useState} from "react";
import styles from "./research-search.module.css";
import {marketFetch} from "./public-transport";

type EvidenceItem={id:string;chunk_id?:string;title:string;excerpt:string;code?:string;company?:string;source_url:string;published_at?:string|null;section?:string|null;page?:number|null;revision:string};
type SearchResult={status:string;mode:string;fallback_reason?:string|null;available_documents:number;items:EvidenceItem[];coverage?:{truncated?:boolean;index?:string};revision:string};
function sourceUrl(value:string){try{const url=new URL(value);return url.protocol==="https:"&&!url.username&&!url.password?url.href:null;}catch{return null;}}

export default function ResearchSearchPanel({query,onOpenStock}:{query:string;onOpenStock:(code:string)=>void}){
 const [kind,setKind]=useState(""),[result,setResult]=useState<SearchResult|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [searched,setSearched]=useState("");
 const pending=useRef<AbortController|null>(null),sequence=useRef(0);
 useEffect(()=>()=>{sequence.current++;pending.current?.abort();},[]);
 async function search(){
  const text=query.trim();if(!text||busy)return;
  const current=++sequence.current;pending.current?.abort();const controller=new AbortController();pending.current=controller;
  const timeout=setTimeout(()=>controller.abort(),10000);setBusy(true);setError("");setSearched(text);setResult(null);
  try{
   const params=new URLSearchParams({q:text,limit:"10"});if(kind)params.set("kind",kind);
   const response=await marketFetch(`/api/market/v1/research/search?${params}`,{signal:controller.signal,cache:"no-store"});
   if(!response.ok)throw new Error("request");const data=await response.json() as SearchResult;
   if(!Array.isArray(data.items)||typeof data.mode!=="string")throw new Error("contract");
   if(current===sequence.current)setResult(data);
  }catch{if(current===sequence.current)setError("자료를 불러오지 못했어요.");}
  finally{clearTimeout(timeout);if(current===sequence.current)setBusy(false);}
 }
 return <section className={styles.panel} aria-label="근거 자료 검색">
  <form id="market-research-form" className={styles.controls} onSubmit={e=>{e.preventDefault();void search();}}>
   <select aria-label="자료 유형" value={kind} onChange={e=>setKind(e.target.value)}><option value="">모든 자료</option><option value="filing">공시</option><option value="ir">기업 IR</option><option value="evidence">기업 근거</option><option value="news">뉴스 제목</option></select>
   <button className="mk-button mk-primary" type="submit" disabled={busy||!query.trim()}>{busy?"검색 중…":"자료 검색"}</button>
  </form>
  <div className={styles.results} aria-live="polite" aria-busy={busy}>
   {!searched&&!busy&&<p className={styles.status}>회사·사업·공시에서 확인할 내용을 입력해 주세요.</p>}
   {busy&&<p className={styles.status} role="status">근거를 찾는 중이에요.</p>}
   {error&&<p className={styles.status} role="alert">{error} <button className="mk-button" onClick={()=>void search()}>다시 시도</button></p>}
   {result&&<>
    <div className={styles.meta}><span>{["hybrid","hybrid_rrf"].includes(result.mode)?"키워드·의미 검색":result.mode==="vector"?"의미 검색":"키워드 검색"}</span><span>{result.items.length}개 결과</span></div>
    {result.coverage?.index==="context_titles_only"&&<p className={styles.status}>문서 제목만 검색해요.</p>}
    {!result.items.length&&<p className={styles.status}>{result.available_documents===0?"검색할 자료가 아직 없어요.":"조건에 맞는 근거가 없어요."}</p>}
    {result.items.map(item=>{const url=sourceUrl(item.source_url);return <article key={item.chunk_id||`${item.id}:${item.revision}`} className={styles.item}>
     <div className={styles.context}>{item.code&&/^[A-Z0-9]{6}$/.test(item.code)?<button onClick={()=>onOpenStock(item.code!)}>{item.company||item.code}</button>:item.company&&<span>{item.company}</span>}{item.published_at&&<time>{item.published_at.slice(0,10)}</time>}</div>
     {url?<a href={url} target="_blank" rel="noopener noreferrer">{item.title} ↗</a>:<strong>{item.title}</strong>}
     {item.excerpt.trim()&&item.excerpt.trim()!==item.title.trim()&&<p className={styles.excerpt}>{item.excerpt}</p>}
     {(item.section&&item.section!=="제목"||item.page!=null)&&<small>{[item.section!=="제목"?item.section:null,item.page!=null?`${item.page}쪽`:null].filter(Boolean).join(" · ")}</small>}
    </article>;})}
    {result.coverage?.truncated&&<p className={styles.status}>회사나 자료 유형을 지정하면 더 정확하게 찾을 수 있어요.</p>}
   </>}
  </div>
 </section>;
}
