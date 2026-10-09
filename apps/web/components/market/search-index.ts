import {request} from "./types";
export type SearchIdentity={code:string;name:string;market:string;logo_url?:string|null;tokens?:string[]};
let cached:SearchIdentity[]|null=null, expires=0, flight:Promise<SearchIdentity[]>|null=null;
export function loadSearchIndex():Promise<SearchIdentity[]> {
  if(cached&&Date.now()<expires)return Promise.resolve(cached);
  if(flight)return flight;
  flight=request<{rows:SearchIdentity[]}>("/search-index").then(data=>{
    if(!Array.isArray(data.rows)||!data.rows.length)throw new Error("Empty search index");
    cached=data.rows;expires=Date.now()+300000;return data.rows;
  }).finally(()=>{flight=null;});
  return flight;
}
export function matchSearchIndex(rows:SearchIdentity[],query:string,limit=8):SearchIdentity[] {
  const needle=query.toLocaleLowerCase().replace(/\s/g,"");
  if(!needle)return [];
  const matches=rows.filter(row=>row.tokens?.some(token=>token.includes(needle)));
  return [...matches.filter(row=>row.code===needle||row.name===query),...matches.filter(row=>row.code!==needle&&row.name!==query)].slice(0,limit);
}
