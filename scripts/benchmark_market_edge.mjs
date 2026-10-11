// Same HTTP/1.1 transport for both addresses; measures public internet latency.
import https from 'node:https';
import fs from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import {gunzipSync} from 'node:zlib';

const hosts=['stocks.62-171-141-206.sslip.io','korea-stocks-edge.lch68-workers.workers.dev'];
const codes=['005930','000660','005380','035420','035720','051910','005490','000270','068270','105560'];
const queries=['삼성','하이닉스','현대','네이버','카카오','LG','포스코','기아','셀트리온','KB'];
const paths={search:queries.map(q=>'/api/market/v1/search?q='+encodeURIComponent(q)),
  summary:codes.map(code=>'/api/market/v1/securities/'+code+'?view=summary'),
  search_index:['/api/market/v1/search-index']};
if(process.env.MARKET_EDGE_QUICK==='1')delete paths.search_index;
const report={at:new Date().toISOString(),concurrency:100,transport:'HTTPS HTTP/1.1 keep-alive from Windows PC',results:[]};
for(const hostname of hosts){
 const agent=new https.Agent({keepAlive:true,maxSockets:100});
 const read=path=>new Promise(resolve=>{
  const start=performance.now();const chunks=[];let firstByte,finished=false;
  const finish=row=>{if(finished)return;finished=true;clearTimeout(timer);resolve(row);};
  const timer=setTimeout(()=>{finish({ms:performance.now()-start,status:0,valid:false});req.destroy();},20000);
  const req=https.get({hostname,path,agent,headers:{'Accept-Encoding':'gzip'}},res=>{
   res.on('data',part=>{firstByte??=performance.now()-start;chunks.push(part);});
   res.on('end',()=>{let valid=false;try{const bytes=Buffer.concat(chunks);const body=res.headers['content-encoding']==='gzip'?gunzipSync(bytes):bytes;JSON.parse(body.toString());valid=true;}catch{}
    finish({ms:performance.now()-start,ttfb:firstByte||0,status:res.statusCode,valid,edge:res.headers['x-market-edge']||'ORIGIN',bytes:chunks.reduce((n,p)=>n+p.length,0)});});
   res.on('error',()=>finish({ms:performance.now()-start,status:0,valid:false}));
   res.on('close',()=>{if(!res.complete)finish({ms:performance.now()-start,status:0,valid:false});});
  });req.on('error',()=>finish({ms:performance.now()-start,status:0,valid:false}));req.setTimeout(20000,()=>req.destroy());
 });
 const summarize=rows=>{const sorted=rows.map(r=>r.ms).sort((a,b)=>a-b);return {requests:rows.length,p50_ms:+sorted[Math.floor(sorted.length*.5)].toFixed(2),p95_ms:+sorted[Math.ceil(sorted.length*.95)-1].toFixed(2),errors:rows.filter(r=>r.status!==200||!r.valid).length,cache:rows.reduce((a,r)=>(a[r.edge||'ERROR']=(a[r.edge||'ERROR']||0)+1,a),{})};};
 for(const [endpoint,choices] of Object.entries(paths)){
  const batch=()=>Promise.all(Array.from({length:100},(_,i)=>read(choices[i%choices.length])));
  const first=await batch();const warm=[];for(let n=0;n<3;n++)warm.push(...await batch());
  await new Promise(resolve=>setTimeout(resolve,1600));const after=await batch();
  report.results.push({hostname,endpoint,first:summarize(first),warm:summarize(warm),after_1600ms:summarize(after)});
  await fs.writeFile(process.argv[2]||'work/edge-benchmark.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report.results.at(-1)));
 }
 agent.destroy();
}
await fs.writeFile(process.argv[2]||'work/edge-benchmark.json',JSON.stringify(report,null,2));
console.log(JSON.stringify(report));
