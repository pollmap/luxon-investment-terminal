"use client";
import {useEffect,useState} from "react";
import {request} from "./types";
import {loadSearchIndex,matchSearchIndex,type SearchIdentity} from "./search-index";

export function useStockSearch(value:string,enabled=true,limit=8){
  const [catalog,setCatalog]=useState<SearchIdentity[]|null>(null);
  const [rows,setRows]=useState<SearchIdentity[]>([]),[busy,setBusy]=useState(false),[failed,setFailed]=useState(false);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{
    if(!enabled)return;
    let live=true;
    loadSearchIndex().then(data=>{if(live)setCatalog(data);}).catch(()=>{});
    return()=>{live=false;};
  },[enabled,revision]);
  useEffect(()=>{
    const abort=new AbortController();setFailed(false);setRows([]);
    if(!enabled||!value.trim()){setBusy(false);return;}
    if(catalog){setRows(matchSearchIndex(catalog,value,limit));setBusy(false);return;}
    setBusy(true);
    const timer=setTimeout(()=>{
      request<{rows:SearchIdentity[]}>(`/search?q=${encodeURIComponent(value.trim())}`,{signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])})
        .then(data=>{if(!abort.signal.aborted)setRows(data.rows.slice(0,limit));})
        .catch(()=>{if(!abort.signal.aborted)setFailed(true);})
        .finally(()=>{if(!abort.signal.aborted)setBusy(false);});
    },100);
    return()=>{clearTimeout(timer);abort.abort();};
  },[value,enabled,catalog,limit,revision]);
  return {rows,busy,failed,retry:()=>setRevision(n=>n+1)};
}
