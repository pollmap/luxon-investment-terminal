/** Saved range IDs stay compatible; labels represent calendar periods. */
export function chartCutoff(lastDay:string,range:number):string {
 const months:Record<number,number>={21:1,63:3,252:12,756:36,1260:60,2600:120};
 if(!months[range])return '';
 const last=new Date(lastDay+'T00:00:00Z');
 if(!Number.isFinite(last.getTime()))return '';
 const target=new Date(Date.UTC(last.getUTCFullYear(),last.getUTCMonth()-months[range],1));
 const finalDay=new Date(Date.UTC(target.getUTCFullYear(),target.getUTCMonth()+1,0)).getUTCDate();
 target.setUTCDate(Math.min(last.getUTCDate(),finalDay));
 return target.toISOString().slice(0,10);
}

/** Use observed sessions only; never synthesize missing minute candles. */
export function intradayWindow<T extends {time?:string;date:string}>(rows:T[],period:string):T[] {
 const ordered=rows.filter(row=>row.time&&Number.isFinite(Date.parse(row.time))).sort((a,b)=>a.time!.localeCompare(b.time!));
 const last=ordered.at(-1)?.time?.slice(0,10);
 if(!last)return [];
 const first=new Date(last+'T00:00:00Z');
 if(period==='week')first.setUTCDate(first.getUTCDate()-6);
 const start=first.toISOString().slice(0,10);
 return ordered.filter(row=>row.time!.slice(0,10)>=start);
}
