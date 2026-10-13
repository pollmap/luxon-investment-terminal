/** Choose an observed point; never synthesize a date or financial value. */
export function closestObservedIndex(times:string[],selected:string|null):number|null {
  if(!times.length||selected===null)return null;
  const exact=times.indexOf(selected);if(exact>=0)return exact;
  const wanted=Date.parse(selected),valid=Number.isFinite(wanted);
  let best=0,distance=Infinity;
  for(let i=0;i<times.length;i++){const at=Date.parse(times[i]);if(valid&&Number.isFinite(at)){const delta=Math.abs(at-wanted);if(delta<distance){distance=delta;best=i;}}else if(times[i]<=selected)best=i;}
  return best;
}
export function preservedLogicalRange(previous:string[],next:string[],range:{from:number;to:number}|null){
  if(!range||!previous.length||!next.length)return null;
  if(next.at(-1)!<previous[0]||next[0]>previous.at(-1)!)return null;
  const anchor=Math.max(0,Math.min(previous.length-1,Math.floor(range.from))),nextAnchor=next.indexOf(previous[anchor]);
  if(nextAnchor>=0){const offset=nextAnchor-anchor;return {from:range.from+offset,to:range.to+offset};}
  const first=closestObservedIndex(next,previous[anchor]),last=closestObservedIndex(next,previous[Math.max(0,Math.min(previous.length-1,Math.ceil(range.to)))]);
  if(first===null||last===null)return null;
  return {from:Math.min(first,last),to:Math.max(first,last)+Number(first===last)};
}
