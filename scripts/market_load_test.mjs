import http from 'node:http';
import fs from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
const agent=new http.Agent({keepAlive:true,maxSockets:100});
const base=new URL(process.env.MARKET_API_URL||'http://127.0.0.1:8210');
if(base.protocol!=='http:'||!['127.0.0.1','localhost'].includes(base.hostname))throw new Error('This benchmark is restricted to a local preview');
const report={base:base.href,measured_at:new Date().toISOString(),client:'Node HTTP, full response bytes, gzip transfer; no JSON rendering',concurrency:100,requests_per_endpoint:300,environment:process.env.MARKET_LOAD_ENVIRONMENT||'local SQLite, single API process; warm cache; not cloud capacity'};
function get(path){return new Promise(resolve=>{const started=performance.now();let bytes=0;const request=http.get({hostname:base.hostname,port:base.port,path:'/api/market/v1'+path,agent,headers:{'Accept-Encoding':'gzip'}},response=>{response.on('data',chunk=>{bytes+=chunk.length;});response.on('end',()=>resolve({ms:performance.now()-started,status:response.statusCode,bytes}));response.on('error',()=>resolve({ms:performance.now()-started,status:0,bytes}));});request.on('error',()=>resolve({ms:performance.now()-started,status:0,bytes}));request.setTimeout(60000,()=>request.destroy());});}
const endpoints=[['search','/search?q='+encodeURIComponent('삼성')],['summary','/securities/005930?view=summary'],['detail','/securities/005930']];
if(process.env.MARKET_LOAD_INCLUDE_ANALYSIS==='1')endpoints.push(['price_context','/securities/005930/analysis/price-context?years=3']);
for(const [name,path] of endpoints){
 await get(path);const results=[];
 await Promise.all(Array.from({length:100},async()=>{for(let i=0;i<3;i++)results.push(await get(path));}));
 const times=results.map(r=>r.ms).sort((a,b)=>a-b);report[name]={p50_ms:+times[149].toFixed(2),p95_ms:+times[284].toFixed(2),max_ms:+times.at(-1).toFixed(2),errors:results.filter(r=>r.status!==200).length,bytes_per_response:results[0].bytes};
}
agent.destroy();await fs.writeFile(process.env.MARKET_LOAD_OUTPUT||'docs/market-load-node-results.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
