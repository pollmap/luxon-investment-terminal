"use client";
import {useEffect, useRef, useState} from "react";
import {Search, X} from "lucide-react";
import {useStockSearch} from "./use-stock-search";
import CompanyLogo from "./company-logo";
import dynamic from "next/dynamic";
import styles from "./research-search.module.css";
const ResearchSearchPanel=dynamic(()=>import("./research-search-panel"),{ssr:false,loading:()=> <p role="status">자료 검색을 여는 중이에요.</p>});

export default function HeaderSearch({onOpen}: {onOpen: (code:string)=>void}) {
  const [value,setValue]=useState("");
  const [open,setOpen]=useState(false), [index,setIndex]=useState(-1);
  const [mode,setMode]=useState<"stocks"|"research">("stocks");
  const {rows,busy,failed,retry}=useStockSearch(value,mode==="stocks"&&(open||!!value.trim()));
  const root=useRef<HTMLDivElement>(null), input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const close=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false);};
    document.addEventListener("pointerdown",close);
    return()=>document.removeEventListener("pointerdown",close);
  },[]);
  useEffect(()=>{setIndex(-1);},[value,rows]);
  const choose=(code:string)=>{setOpen(false);setValue("");onOpen(code);};
  return <div ref={root} className="mk-header-search">
    <Search size={18}/><input ref={input} role={mode==="stocks"?"combobox":undefined} aria-label={mode==="stocks"?"종목명 또는 코드 검색":"근거 자료 검색어"} aria-expanded={mode==="stocks"?open&&!!value.trim():undefined} aria-controls={mode==="stocks"?"header-search-results":undefined} aria-autocomplete={mode==="stocks"?"list":undefined} aria-activedescendant={mode==="stocks"&&index>=0?`stock-option-${index}`:undefined} form={mode==="research"?"market-research-form":undefined} maxLength={mode==="research"?200:100}
      placeholder="종목·자료 검색" value={value} onFocus={()=>setOpen(true)} onChange={e=>{setValue(e.target.value);setOpen(true);}}
      onKeyDown={e=>{if(e.nativeEvent.isComposing)return;if(e.key==="Escape"){setOpen(false);e.stopPropagation();}else if(mode==="research"&&e.key==="Enter"){e.preventDefault();(document.getElementById("market-research-form") as HTMLFormElement|null)?.requestSubmit();}else if(mode==="stocks"&&e.key==="ArrowDown"){e.preventDefault();setOpen(true);setIndex(i=>Math.min(rows.length-1,i+1));}else if(mode==="stocks"&&e.key==="ArrowUp"){e.preventDefault();setIndex(i=>rows.length?Math.max(0,i-1):-1);}else if(mode==="stocks"&&e.key==="Enter"&&rows[index>=0?index:0]){e.preventDefault();choose(rows[index>=0?index:0].code);}}}/>
    {value&&<button aria-label="검색어 지우기" onClick={()=>{setValue("");input.current?.focus();}}><X size={16}/></button>}
    {open&&value.trim()&&<div className={`mk-header-results ${mode==="research"?styles.wide:""}`}>
      <div className={styles.switcher} aria-label="검색 대상"><button aria-pressed={mode==="stocks"} onClick={()=>{setMode("stocks");input.current?.focus();}}>종목</button><button aria-pressed={mode==="research"} onClick={()=>{setMode("research");input.current?.focus();}}>자료</button></div>
      {mode==="research"?<ResearchSearchPanel query={value} onOpenStock={choose}/>:<><div id="header-search-results" role="listbox" aria-label="검색 결과">{rows.map((s,i)=><div key={s.code} id={`stock-option-${i}`} role="option" aria-selected={index===i} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(s.code)}>
        <CompanyLogo url={s.logo_url} name={s.name}/><span><strong>{s.name}</strong><small>{s.code} · {s.market}</small></span>
      </div>)}</div>
      {!rows.length&&<p role="status">{busy?"검색 중…":failed?"검색 결과를 불러오지 못했어요.":"검색 결과가 없어요."}{failed&&<button className="mk-button" onClick={retry}>다시 시도</button>}</p>}</>}
    </div>}
  </div>;
}
