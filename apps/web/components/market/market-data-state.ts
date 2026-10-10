export type RankingRow={code:string;name:string;rank:number;price:number|null;change:number|null;value:number|null;volume:number|null;unranked?:boolean};
export type Ranking={rows:RankingRow[];status:string;as_of?:string|null;scope?:string;change_basis?:string};
export type RankingOrder={key:string;rows:Pick<RankingRow,'code'|'name'|'rank'>[]};

// Freeze reading order, never an earlier response's values or availability.
export function captureRankingOrder(previous:RankingOrder|null,key:string,data:Ranking):RankingOrder|null{
  if(!data.rows.length)return null;
  return previous?.key===key?previous:{key,rows:data.rows.map(({code,name,rank})=>({code,name,rank}))};
}

export function rankingView(data:Ranking,key:string,order:RankingOrder|null):Ranking{
  if(order?.key!==key||!data.rows.length)return data;
  const latest=new Map(data.rows.map(row=>[row.code,row]));
  return {...data,rows:order.rows.map(slot=>{
    const row=latest.get(slot.code);
    return row?{...row,...slot,unranked:false}:{...slot,price:null,change:null,value:null,volume:null,unranked:true};
  })};
}

export function marketDataFailed(status:string,networkError=false):boolean{
  return networkError||status==='error'||status==='stale';
}
