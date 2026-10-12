import type {Page} from '@playwright/test';

// Deterministic fixture_non_production data. It verifies rendering and request
// contracts, never real company facts, TTM coverage, or provider availability.
export type AnalysisFixtureOptions={ineligible?:boolean;financialError?:boolean;financialUnavailable?:boolean};
const trace={source:'fixture_non_production',source_document_id:'fixture-analysis-document',filing_id:'20260302000001',available_at:'2026-03-02T09:00:00+09:00',period:'2025',unit:'원',currency:'KRW',method:'fixture',formula:'fixture only'};
const definitions=[
 ['EPS','기본 EPS','이익',15,true],['EPS_DILUTED','희석 EPS','이익',15,true],['EPS_ADJUSTED','조정 EPS','이익',null,true],
 ['OCF','영업현금흐름/주','현금흐름',null,true],['FCF','잉여현금흐름/주','현금흐름',null,true],['FCFE','FCFE/주','현금흐름',null,true],
 ['FFO','FFO/주','현금흐름',null,true],['AFFO','AFFO/주','현금흐름',null,true],
 ['EBIT','EBIT/주','이익',null,true],['EBITDA','EBITDA/주','이익',null,true],['SALES','매출/주','매출·자산',null,true],['BPS','BPS','매출·자산',1,false],
].map(([code,label,group,default_multiple,supports_ttm])=>({code:String(code),label:String(label),group:String(group),default_multiple:default_multiple as number|null,supports_ttm:Boolean(supports_ttm),available:true,description:'비운영 시험용 지표예요.',formula:'fixture_non_production'}));
const metricValues:Record<string,number>={EPS:10,EPS_DILUTED:9,EPS_ADJUSTED:11,OCF:20,FCF:12,FCFE:13,FFO:14,AFFO:15,EBIT:17,EBITDA:30,SALES:100,BPS:50};
const dates=['2026-03-31','2026-04-30','2026-05-29','2026-06-30','2026-10-08'];
function stock(code:string){return {code,name:code==='005380'?'현대차':'삼성전자',market:'KOSPI',price:200,change:1,volume:10,value:2000,cap:10000,foreign:null,per:null,pbr:null,roe:null,dividend:null,foreign_net:null,institution_net:null,revenue:null,operating_profit:null,as_of:'2026-10-08T15:30:00+09:00',received_at:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',source_trace:trace,quote_trace:trace,facts:{}};}
function valuation(code:string,query:URLSearchParams,options:AnalysisFixtureOptions){
 const metric=query.get('metric')||'EPS',basis=query.get('basis')==='OFS'?'OFS':'CFS',negative=code==='005380'&&metric==='OCF';
 const value=negative?-20:metricValues[metric]??10,definition=definitions.find(row=>row.code===metric)??definitions[0];
 const multiple=definition.default_multiple??8;
 const series=dates.map((date,index)=>{const eligible=!options.ineligible&&!negative&&index<4,price=160+index*10;
   return {date,price,metric_value:value,reference_price:eligible?value*multiple:null,historical_reference:eligible?value*8:null,multiple:eligible?price/value:null,period_end:'2025-12-31',comparison_eligible:eligible,reason:negative?'non_positive_metric':!eligible?'adjustment_or_venue_unverified':undefined};});
 return {fixture_non_production:true,code,metric,metric_definition:definition,multiple,basis,frequency:'annual',mode:query.get('mode')||'as_known',
   share_basis:{adjustment:'raw',per_share_basis_end:'2026-06-30'},series,
   periods:[2024,2025].map(year=>({period:String(year),period_start:`${year}-01-01`,period_end:`${year}-12-31`,frequency:'annual',basis,value:year===2024?value*.8:value,available_at:`${year+1}-03-02T00:00:00Z`,reason:options.ineligible?'adjustment_or_venue_unverified':undefined,source_trace:{...trace,period:String(year),available_at:`${year+1}-03-02T00:00:00Z`,filing_id:`${year+1}0302000001`,source_document_id:`fixture-${metric}-${year}`}})),
   summary:{representative_multiple:8,monthly_observations:24},availability:{comparison:series.some(point=>point.comparison_eligible),reasons:negative?['non_positive_metric']:options.ineligible?['adjustment_or_venue_unverified']:[]},
   ttm:{rows:[]},revision:'fixture-analysis-v1'};
}
function financialQuestions(code:string,query:URLSearchParams,unavailable=false){
 const row=(metric:string,name:string,value:number|null)=>({metric,name,status:value===null?'unavailable':'ready',value,unit:'원',period_start:'2025-01-01',period_end:'2025-12-31',reason_label:value===null?'확인된 입력이 없어요':undefined,source_trace:value===null?null:trace,formula:'fixture_non_production'});
 return {fixture_non_production:true,code,basis:query.get('basis')==='OFS'?'OFS':'CFS',frequency:'annual',status:unavailable?'unavailable':'partial',period_start:'2025-01-01',period_end:'2025-12-31',
   rows:[row('net_income','당기순이익',unavailable?null:10000000000),row('operating_cash','영업현금흐름',unavailable?null:15000000000),row('capex','유형·무형자산 취득 지출',null)],
   comparisons:[{...row('cash_conversion','순이익 대비 영업현금흐름',unavailable?null:150),unit:'%'}]};
}
export async function installAnalysisFixture(page:Page,options:AnalysisFixtureOptions={}){
 const calls:string[]=[],blockedExternal:string[]=[];
 await page.addInitScript(()=>Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true}));
 // Allow only the local static app. API requests, including a baked-in public
 // origin, are intercepted by the more specific route registered below.
 await page.route('**/*',route=>{const url=new URL(route.request().url());if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname)){blockedExternal.push(url.origin);return route.abort('blockedbyclient');}return route.continue();});
 await page.routeWebSocket('**/*',()=>{});
 await page.route('**/api/market/v1/**',async route=>{
   const url=new URL(route.request().url()),path=url.pathname.replace('/api/market/v1',''),code=/^\/securities\/(\d{6})/.exec(path)?.[1]??'005930';
   calls.push(path+url.search);let status=200,data:unknown={fixture_non_production:true,status:'no_data',rows:[]};
   if(path==='/metrics')data={};
   else if(path==='/search-index')data={fixture_non_production:true,rows:['005930','005380'].map(code=>({code,name:stock(code).name,market:'KOSPI',tokens:[code,stock(code).name]}))};
   else if(path==='/screener'||path==='/search')data={fixture_non_production:true,rows:[stock('005930'),stock('005380')],total:2};
   else if(path==='/overview')data={fixture_non_production:true,indices:[],breadth:{up:2,down:0,flat:0},total:2,value:2000,sectors:[],filings:{rows:[],status:'no_data'}};
   else if(/^\/securities\/\d{6}$/.test(path))data={fixture_non_production:true,security:stock(code),detail:{financials:[],flows:[],source_trace:trace},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
   else if(path.endsWith('/quote'))data=stock(code);
   else if(path.endsWith('/depth'))data={fixture_non_production:true,book:[],trades:[],book_status:'no_data',trades_status:'no_data',venue:'KRX'};
   else if(path.endsWith('/analysis/metrics'))data={fixture_non_production:true,metrics:definitions};
   else if(path.endsWith('/analysis/valuation'))data=valuation(code,url.searchParams,options);
   else if(path.endsWith('/analysis/financial-questions')){status=options.financialError?503:200;data=financialQuestions(code,url.searchParams,options.financialUnavailable);}
   else if(path.endsWith('/chart'))data={fixture_non_production:true,status:'ready',session_date:'2026-10-08',data_as_of:'2026-10-08T15:30:00+09:00',points:[{time:'20261008090000',value:190},{time:'20261008153000',value:200}]};
   else if(/\/(bars|price-window)$/.test(path))data={fixture_non_production:true,status:'ready',venue:'KRX',rows:dates.map((date,index)=>({date,time:date,open:160+index*10,high:175+index*10,low:150+index*10,close:165+index*10,volume:10,ohlc_valid:true,ma20:null}))};
   else if(path==='/analysis/scenario'){const input=route.request().postDataJSON();data={fixture_non_production:true,rows:Array.from({length:Number(input.years)||1},(_,index)=>{const year=index+1,value=Number(input.base)*(1+Number(input.growth)/100)**year;return {year,metric_value:value,reference_price:value*Number(input.multiple)};})};}
   await route.fulfill({status,json:data,headers:{'Cache-Control':'no-store'}});
 });
 return {calls,options,blockedExternal};
}
