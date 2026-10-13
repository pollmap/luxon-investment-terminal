export type PriceHistoryCoverage={
 requested:{start:string|null;end:string|null};displayed:{start:string|null;end:string|null;rows:number};stored:{start:string|null;end:string|null;rows:number};
 selection:{reason:string;venue?:string;adjustment?:string;ohlc_edges_available?:boolean};
 collection:{status:string;history_complete:boolean;backfill_complete:boolean;older_pages_pending:boolean;gap_pending:boolean;gap_status:string|null;head_received_at?:string};
 verification:{status:string;reason:string|null};
};
export function historyNotice(coverage:PriceHistoryCoverage|undefined):string|null {
 if(!coverage)return null;
 const {requested,stored,collection}=coverage;
 if(collection.gap_pending)return collection.gap_status==='collecting'?'빠진 가격 구간을 가져오는 중이에요.':'중간에 확인되지 않은 가격 구간이 있어요.';
 const shorter=Boolean(requested.start&&stored.start&&requested.start<stored.start);
 if(shorter||requested.start===null&&!collection.backfill_complete){
  if(['collecting','pending'].includes(collection.status))return '이전 가격을 가져오는 중이에요.';
  if(collection.older_pages_pending)return '이전 가격 자료가 더 남아 있어요.';
  return shorter?'요청한 기간의 일부 가격만 있어요.':null;
 }
 return null;
}
