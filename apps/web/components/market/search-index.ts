import {readSearchIndex} from "./public-transport";
export type SearchIdentity={code:string;name:string;market:string;logo_url?:string|null;tokens?:string[]};
let cached:SearchIdentity[]|null=null, expires=0, flight:Promise<SearchIdentity[]>|null=null;

function validateIndex(body:unknown):SearchIdentity[] {
  const rows=body&&typeof body==='object'&&'rows' in body?body.rows:null;
  if(!Array.isArray(rows)||!rows.length)throw new Error("Empty search index");
  const codes=new Set<string>();
  for(const row of rows){
    if(!row||typeof row!=='object'||typeof row.code!=='string'||!/^[A-Z0-9]{6}$/.test(row.code)||
       codes.has(row.code)||typeof row.name!=='string'||!row.name.trim()||
       !['KOSPI','KOSDAQ','코스피','코스닥'].includes(row.market)||
       !Array.isArray(row.tokens)||!row.tokens.length||row.tokens.some((token:unknown)=>typeof token!=='string'||!token)||
       (row.logo_url!=null&&typeof row.logo_url!=='string'))throw new Error("Invalid search index");
    codes.add(row.code);
  }
  return rows;
}

export function loadSearchIndex():Promise<SearchIdentity[]> {
  if(cached&&Date.now()<expires)return Promise.resolve(cached);
  if(flight)return flight;
  flight=readSearchIndex(validateIndex).then(({data,expiresAt})=>{
    cached=data;expires=expiresAt;return data;
  }).finally(()=>{flight=null;});
  return flight;
}
export function matchSearchIndex(rows:SearchIdentity[],query:string,limit=8):SearchIdentity[] {
  const needle=query.toLocaleLowerCase().replace(/\s/g,"");
  if(!needle)return [];
  const matches=rows.filter(row=>row.tokens?.some(token=>token.includes(needle)));
  return [...matches.filter(row=>row.code===needle||row.name===query),...matches.filter(row=>row.code!==needle&&row.name!==query)].slice(0,limit);
}
