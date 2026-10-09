"use client";
import {useEffect, useRef, useState} from "react";
import {Search, X} from "lucide-react";
import {useStockSearch} from "./use-stock-search";
import CompanyLogo from "./company-logo";

export default function HeaderSearch({onOpen}: {onOpen: (code:string)=>void}) {
  const [value,setValue]=useState("");
  const {rows,busy,failed,retry}=useStockSearch(value);
  const [open,setOpen]=useState(false), [index,setIndex]=useState(-1);
  const root=useRef<HTMLDivElement>(null), input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const close=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false);};
    document.addEventListener("pointerdown",close);
    return()=>document.removeEventListener("pointerdown",close);
  },[]);
  useEffect(()=>{setIndex(-1);},[value,rows]);
  const choose=(code:string)=>{setOpen(false);setValue("");onOpen(code);};
  return <div ref={root} className="mk-header-search">
    <Search size={18}/><input ref={input} role="combobox" aria-label="종목명 또는 코드 검색" aria-expanded={open&&!!value.trim()} aria-controls="header-search-results" aria-autocomplete="list" aria-activedescendant={index>=0?`stock-option-${index}`:undefined}
      placeholder="종목명·코드·초성 검색" value={value} onFocus={()=>setOpen(true)} onChange={e=>{setValue(e.target.value);setOpen(true);}}
      onKeyDown={e=>{if(e.nativeEvent.isComposing)return;if(e.key==="Escape"){setOpen(false);e.stopPropagation();}else if(e.key==="ArrowDown"){e.preventDefault();setOpen(true);setIndex(i=>Math.min(rows.length-1,i+1));}else if(e.key==="ArrowUp"){e.preventDefault();setIndex(i=>rows.length?Math.max(0,i-1):-1);}else if(e.key==="Enter"&&rows[index>=0?index:0]){e.preventDefault();choose(rows[index>=0?index:0].code);}}}/>
    {value&&<button aria-label="검색어 지우기" onClick={()=>{setValue("");input.current?.focus();}}><X size={16}/></button>}
    {open&&value.trim()&&<div className="mk-header-results">
      <div id="header-search-results" role="listbox" aria-label="검색 결과">{rows.map((s,i)=><div key={s.code} id={`stock-option-${i}`} role="option" aria-selected={index===i} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(s.code)}>
        <CompanyLogo url={s.logo_url} name={s.name}/><span><strong>{s.name}</strong><small>{s.code} · {s.market}</small></span>
      </div>)}</div>
      {!rows.length&&<p role="status">{busy?"검색 중…":failed?"검색 결과를 불러오지 못했어요.":"검색 결과가 없어요."}{failed&&<button className="mk-button" onClick={retry}>다시 시도</button>}</p>}
    </div>}
  </div>;
}
