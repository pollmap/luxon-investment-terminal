"use client";
// Keyboard/pointer separator interaction adapted from Lucian #101, MIT (c)
// 2026 Seokwoooo. Source commit 42396145b375a17712fd738ca0e1d977556c0b73.
// Notice: /stocks/licenses/lucian-MIT.txt. No demo artwork/data is included.
import {useEffect,useState,type CSSProperties,type ReactNode} from 'react';
export function clampPanelWidth(value:number,min:number,max:number){return Math.max(min,Math.min(max,Math.round(value)));}
export function usePanelWidth(name:string,initial:number,min:number,max:number){
 const [width,setWidth]=useState(initial);
 useEffect(()=>{try{const saved=Number(localStorage.getItem(`market:panel-width:${name}`));if(saved>=min&&saved<=max)setWidth(saved);}catch{}},[name,min,max]);
 const update=(value:number)=>{const next=clampPanelWidth(value,min,max);setWidth(next);try{localStorage.setItem(`market:panel-width:${name}`,String(next));}catch{}};
 return [width,update] as const;
}
export function PaneSeparator({label,width,onWidth,initial,min,max,container,side='left'}:{label:string;width:number;onWidth:(value:number)=>void;initial:number;min:number;max:number;container:string;side?:'left'|'right'}){
 return <button type="button" className="mk-pane-separator" role="separator" aria-label={`${label} · 방향키로 조절`} aria-orientation="vertical" aria-valuemin={min} aria-valuemax={max} aria-valuenow={width} aria-valuetext={`${width}px`} title="방향키로 조절 · Esc로 복원"
  onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))return;event.preventDefault();const delta=(event.key==='ArrowRight'?1:-1)*(side==='right'?-1:1)*(event.shiftKey?48:16);onWidth(event.key==='Escape'?initial:event.key==='Home'?min:event.key==='End'?max:width+delta);}}
  onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);}}
  onPointerMove={event=>{if(!event.currentTarget.hasPointerCapture(event.pointerId))return;const bounds=event.currentTarget.closest(container)?.getBoundingClientRect();if(bounds)onWidth(side==='right'?bounds.right-event.clientX:event.clientX-bounds.left);}}
  onPointerUp={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}><span aria-hidden="true">⋮</span></button>;
}
export default function WorkspaceSplit({list,children}:{list:ReactNode;children:ReactNode}){
 const [width,setWidth]=usePanelWidth('list',240,200,360);
 return <div className="mk-detail-workspace" style={{'--mk-list-width':`${width}px`} as CSSProperties}>{list}<PaneSeparator label="종목 목록 너비" width={width} onWidth={setWidth} initial={240} min={200} max={360} container=".mk-detail-workspace"/>{children}</div>;
}
