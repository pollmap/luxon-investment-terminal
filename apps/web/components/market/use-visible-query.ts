"use client";
import {useEffect, useState} from "react";
import {request} from "./types";

// A null path is a closed panel. Keep its last value, but stop all network work.
export function useVisibleQuery<T>(path:string|null, interval=60000) {
  const [state,setState]=useState<{path:string;data:T|null;error:boolean}|null>(null);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{
    if(!path)return;
    const abort=new AbortController();let busy=false;
    const load=async()=>{
      if(document.hidden||busy||abort.signal.aborted)return;
      busy=true;
      try{
        const data=await request<T>(path,{signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});
        if(!abort.signal.aborted)setState({path,data,error:false});
      }catch{
        if(!abort.signal.aborted)setState(previous=>({path,data:previous?.path===path?previous.data:null,error:true}));
      }finally{busy=false;}
    };
    void load();
    const timer=setInterval(()=>void load(),interval);
    document.addEventListener("visibilitychange",load);
    return()=>{abort.abort();clearInterval(timer);document.removeEventListener("visibilitychange",load);};
  },[path,interval,revision]);
  return {data:state?.path===path?state.data:null,error:state?.path===path&&state.error,retry:()=>setRevision(n=>n+1)};
}
