import http2 from 'node:http2';
import fs from 'node:fs/promises';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {gunzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {applicationTiming, percentile95, responseDiagnostics} from './market_benchmark_metrics.mjs';

export const measurementContract = {
 scope:'API endpoint load; not the browser search/detail user flow',
 phases:{first:'First batch; not guaranteed cold',warm:'Three subsequent batches; not guaranteed cache hits',after_1600ms:'Batch after a 1600ms pause; classify expiry from response headers'},
 request_headers:{'accept-encoding':'gzip',origin:null,'if-none-match':null,'cache-control':null},
 browser_behaviour_included:false,
 connection_setup_in_request_timing:false,
 timing:'TTFB includes proxy/queue/transit; app_ms is current uncached application-header time. Do not subtract independent percentiles.',
 origin_header:'On edge HIT the origin cache header is historical, not a new origin lookup',
};

export function summarizeRows(rows) {
 const times=rows.map(r=>r.ms).filter(Number.isFinite).sort((a,b)=>a-b);
 const app=rows.map(r=>r.app_ms).filter(Number.isFinite).sort((a,b)=>a-b);
 const counts=key=>Object.fromEntries([...new Set(rows.map(r=>r[key]||'ERROR'))].sort().map(state=>[state,rows.filter(r=>(r[key]||'ERROR')===state).length]));
 return {requests:rows.length,ttfb_p95_ms:percentile95(rows.map(r=>r.ttfb_ms)),body_p95_ms:percentile95(rows.map(r=>r.body_ms)),
  wire_bytes:rows.reduce((n,r)=>n+(r.wire_bytes||0),0),origin_cache:counts('origin_cache'),
  p50_ms:times.length?+times[Math.floor(times.length*.5)].toFixed(2):null,p95_ms:percentile95(times),
  app_timing_samples:app.length,app_p95_ms:app.length?app[Math.ceil(app.length*.95)-1]:null,
  errors:rows.filter(r=>r.status!==200||!r.valid).length,cache:counts('edge')};
}

export function classifyCache(rows) {
 const group=fields=>{
  const buckets=new Map();
  for(const row of rows){
   const states=fields.map(field=>row[field]||'ERROR'),key=JSON.stringify(states);
   if(!buckets.has(key))buckets.set(key,{states,rows:[]});
   buckets.get(key).rows.push(row);
  }
  return [...buckets.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([,bucket])=>({
   states:Object.fromEntries(fields.map((field,i)=>[field,bucket.states[i]])),...summarizeRows(bucket.rows),
  }));
 };
 return {by_edge:group(['edge']),by_origin_header:group(['origin_cache']),by_edge_and_origin:group(['edge','origin_cache'])};
}

export function summarizeEndpoint(hostname,endpoint,samples) {
 const summarize=rows=>({...summarizeRows(rows),cache_breakdown:classifyCache(rows)});
 return {hostname,endpoint,first:summarize(samples.first),warm:summarize(samples.warm),
  after_1600ms:summarize(samples.expired),samples};
}

export async function runBenchmark() {
if(process.argv.length>3||process.argv[2]?.includes('://')||(process.argv[2]&&!process.argv[2].endsWith('.json')))
 throw new Error('Usage: node scripts/benchmark_market_http2.mjs [output.json]; set hosts with MARKET_BENCH_HOSTS');
const output=path.resolve(process.argv[2]||'work/http2-benchmark.json');
const pending=output+'.partial';
await fs.mkdir(path.dirname(output),{recursive:true});
try{await fs.access(output);throw new Error('Benchmark output already exists; choose a new evidence path');}
catch(error){if(error.code!=='ENOENT')throw error;}
const hosts=(process.env.MARKET_BENCH_HOSTS||'stocks.62-171-141-206.sslip.io,korea-stocks-edge.lch68-workers.workers.dev').split(',').filter(Boolean);
const codes=['005930','000660','005380','035420','035720','051910','005490','000270','068270','105560'];
const queries=['삼성','하이닉스','현대','네이버','카카오','LG','포스코','기아','셀트리온','KB'];
const cacheProbe=process.env.MARKET_BENCH_CACHE_PROBE||'';
if(cacheProbe&&!/^[a-z0-9-]{1,48}$/.test(cacheProbe))throw new Error('Invalid cache probe identifier');
const report={at:new Date().toISOString(),concurrency:100,cache_probe:cacheProbe||null,transport:'HTTP/2 one TLS session, 100 concurrent streams, Windows PC public internet',measurement_contract:measurementContract,results:[]};
// Fail before generating public load if evidence cannot be saved. Preserve a
// checkpoint after each endpoint so a later transport/disk failure loses no
// completed batch and can never be mistaken for a complete measurement.
await fs.writeFile(pending,JSON.stringify({...report,complete:false},null,2),{flag:'wx'});
for(const hostname of hosts){
 const session=http2.connect('https://'+hostname,{settings:{initialWindowSize:1024*1024}});session.on('error',()=>{});
 await new Promise((resolve,reject)=>{session.once('connect',resolve);session.once('error',reject);});
 if(session.alpnProtocol!=='h2')throw new Error('HTTP/2 not negotiated');
 session.setLocalWindowSize(16*1024*1024);
 const read=path=>new Promise(resolve=>{
  const start=performance.now(),chunks=[];let headers={},ended=false,ttfb=null,headersAtMs=null;
  const stream=session.request({':path':path,'accept-encoding':'gzip'});
  const finish=result=>{if(ended)return;ended=true;clearTimeout(timer);resolve({...result,request_path:path});};
  const timer=setTimeout(()=>{finish({ms:performance.now()-start,status:0});stream.close();},20000);
  stream.on('response',value=>{headers=value;ttfb=performance.now()-start;headersAtMs=Date.now();});stream.on('data',chunk=>chunks.push(chunk));
  stream.on('end',()=>{let valid=false;try{let body=Buffer.concat(chunks);if(headers['content-encoding']==='gzip')body=gunzipSync(body);JSON.parse(body.toString());valid=true;}catch{}
   const elapsed=performance.now()-start,originCache=headers['x-market-cache']||'BYPASS';
   const edge=headers['x-market-edge']||'ORIGIN';
   finish({ms:elapsed,ttfb_ms:ttfb,body_ms:ttfb===null?null:elapsed-ttfb,wire_bytes:chunks.reduce((n,c)=>n+c.length,0),status:headers[':status'],valid,
    app_ms:applicationTiming(headers),
    origin_cache:originCache,edge,...responseDiagnostics(headers,headersAtMs,Date.now())});});
  stream.on('error',()=>finish({ms:performance.now()-start,status:0}));stream.end();
 });
 for(const [endpoint,paths] of Object.entries({search:queries.map(q=>'/api/market/v1/search?q='+encodeURIComponent(q)),summary:codes.map(c=>'/api/market/v1/securities/'+c+'?view=summary')})){
  const batch=()=>Promise.all(Array.from({length:100},(_,i)=>read(paths[i%paths.length]+(cacheProbe?'&cache_probe='+cacheProbe:''))));
  const first=await batch(),warm=[];for(let n=0;n<3;n++)warm.push(...await batch());
  await new Promise(resolve=>setTimeout(resolve,1600));const expired=await batch();
  const row=summarizeEndpoint(hostname,endpoint,{first,warm,expired});
  report.results.push(row);const {samples,...compact}=row;console.log(JSON.stringify(compact));
  await fs.writeFile(pending,JSON.stringify({...report,complete:false},null,2));
 }
 session.close();
}
await fs.writeFile(pending,JSON.stringify({...report,complete:true},null,2));
await fs.rename(pending,output);
}

// Importing the pure classifiers never creates evidence files or public load.
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await runBenchmark();
