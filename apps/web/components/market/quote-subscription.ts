"use client";
import {request, type Stock} from "./types";
import {streamUrl} from "./public-transport";

// Each visible view subscribes once. The server shares its provider and row reader.
export function subscribeQuotes(codes:string[], receive:(rows:Stock[])=>void) {
  let active=true, ws:WebSocket|null=null, retry=0, busy=false;
  let reconnect:ReturnType<typeof setTimeout>|undefined;
  let streamVersion=0;
  let epoch="", sequences:Record<string,number>={};
  const abort=new AbortController();
  const snapshot=async()=>{
    if(!active||document.hidden||busy)return;
    busy=true;
    const startedAtVersion=streamVersion;
    try {
      const rows=codes.length===1
        ? [await request<Stock>(`/securities/${codes[0]}/quote`,{signal:abort.signal})]
        : (await request<{rows:Stock[]}>("/screener",{method:"POST",body:JSON.stringify({codes,limit:40}),signal:abort.signal})).rows;
      if(active&&!document.hidden&&streamVersion===startedAtVersion)receive(rows);
    } catch { /* Retry while preserving last good prices. */ } finally {busy=false;}
  };
  const connect=()=>{
    if(!active||document.hidden||ws)return;
    const socket=new WebSocket(streamUrl());
    ws=socket;
    socket.onopen=()=>{if(ws!==socket)return;retry=0;epoch="";sequences={};socket.send(JSON.stringify({codes}));void snapshot();};
    socket.onmessage=event=>{
      if(!active||ws!==socket||document.hidden)return;
      try {
        const message=JSON.parse(event.data);
        if(message.type!=="snapshot"||!Array.isArray(message.rows))return;
        if(message.epoch&&epoch&&epoch!==message.epoch){socket.close();return;}
        epoch=message.epoch||epoch;
        const accepted=message.rows.filter((row:Stock)=>{
          if(!codes.includes(row.code))return false;
          const seq=message.sequences?.[row.code];
          if(typeof seq!=="number")return true; // Legacy server compatibility.
          if(seq<=(sequences[row.code]??-1))return false;
          if(sequences[row.code]!=null&&seq>sequences[row.code]+1)void snapshot();
          sequences[row.code]=seq;return true;
        });
        if(accepted.length){streamVersion++;receive(accepted);}
      } catch {void snapshot();}
    };
    socket.onclose=()=>{if(ws!==socket)return;ws=null;if(active&&!document.hidden){void snapshot();reconnect=setTimeout(connect,Math.min(30000,1000*2**Math.min(retry++,5)));}};
    socket.onerror=()=>socket.close();
  };
  const visible=()=>{
    clearTimeout(reconnect);
    if(document.hidden){const old=ws;ws=null;old?.close();}
    else {void snapshot();connect();}
  };
  const timer=setInterval(()=>{if(!ws||ws.readyState!==WebSocket.OPEN)void snapshot();},5000);
  document.addEventListener("visibilitychange",visible);
  void snapshot();connect();
  return ()=>{active=false;abort.abort();clearInterval(timer);clearTimeout(reconnect);document.removeEventListener("visibilitychange",visible);const old=ws;ws=null;old?.close();};
}
