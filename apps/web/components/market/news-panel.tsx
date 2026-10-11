"use client";
import {useEffect,useState} from "react";
import {request} from "./types";
type News={rows:{title:string;url:string;publisher:string|null;published_at:string|null}[];status:string};
export default function NewsPanel({code}:{code:string}){
 const [data,setData]=useState<News|null>(null),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
 useEffect(()=>{
  const abort=new AbortController();let busy=false;
  const load=async()=>{if(busy||document.hidden)return;busy=true;try{const next=await request<News>(`/securities/${code}/news`,{signal:abort.signal});if(!abort.signal.aborted){setData(next);setFailed(false);}}catch{if(!abort.signal.aborted)setFailed(true);}finally{busy=false;}};
  void load();const timer=setInterval(load,15000);document.addEventListener('visibilitychange',load);
  return()=>{abort.abort();clearInterval(timer);document.removeEventListener('visibilitychange',load);};
 },[code,retry]);
 return <section aria-label="관련 뉴스"><h3>관련 뉴스</h3>
  {(failed||data?.status==='error'||data?.status==='stale')&&<p role="status">{data?.rows.length?'갱신하지 못해 마지막 목록을 표시해요.':'뉴스를 불러오지 못했어요.'} <button className="mk-button" onClick={()=>setRetry(n=>n+1)}>다시 시도</button></p>}
  {!failed&&(!data||data.status==='pending')&&<p role="status">뉴스를 가져오는 중이에요.</p>}
  {data?.status==='no_data'&&<p>이 종목에 연결된 뉴스가 없어요.</p>}
  {data?.rows.map(row=><a className="mk-document" key={row.url} href={row.url} target="_blank" rel="noreferrer"><strong>{row.title}</strong><small>{row.publisher} · {row.published_at?.slice(0,16).replace('T',' ')||'발행일 미확인'} ↗</small></a>)}
 </section>;
}
