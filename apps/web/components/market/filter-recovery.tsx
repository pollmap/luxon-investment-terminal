"use client";
import {useEffect,useMemo,useState} from 'react';
import {labels,request,type Query} from './types';
export function recoveryChoices(query:Query){
 const choices:{label:string;query:Query}[]=[];
 if(query.q)choices.push({label:'검색어 지우기',query:{...query,q:''}});
 if(query.sector)choices.push({label:'분야 조건 빼기',query:{...query,sector:undefined}});
 if(query.rules.length){const removed=query.rules.at(-1)!;choices.push({label:`${labels[removed.metric]} 조건 빼기`,query:{...query,rules:query.rules.slice(0,-1)}});}
 if(query.any_rules.length)choices.push({label:'OR 조건 빼기',query:{...query,any_rules:[]}});
 if(query.market!=='ALL')choices.push({label:'전체 시장으로',query:{...query,market:'ALL'}});
 return choices.slice(0,3);
}
/** Recovery counts are returned by the same screener, never estimated locally. */
export default function FilterRecovery({query,onApply}:{query:Query;onApply:(query:Query)=>void}){
 const queryKey=JSON.stringify(query),choices=useMemo(()=>recoveryChoices(JSON.parse(queryKey)),[queryKey]);
 const [counts,setCounts]=useState<(number|null)[]>([]);
 useEffect(()=>{const abort=new AbortController();setCounts(choices.map(()=>null));choices.forEach((choice,index)=>{void request<{total:number}>('/screener',{method:'POST',body:JSON.stringify({...choice.query,limit:1,offset:0}),signal:abort.signal}).then(result=>{if(!abort.signal.aborted&&Number.isInteger(result.total)&&result.total>=0)setCounts(old=>{const next=[...old];next[index]=result.total;return next;});}).catch(()=>{});});return()=>abort.abort();},[choices]);
 if(!choices.length)return null;
 return <div className="mk-filter-recovery" aria-label="조건 바꾸기">{choices.map((choice,index)=><button className="mk-button" key={choice.label} disabled={counts[index]===0} onClick={()=>onApply(choice.query)}>{choice.label}{counts[index]!=null?` · ${counts[index]!.toLocaleString()}개`:''}</button>)}</div>;
}
