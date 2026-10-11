import http2 from 'node:http2';
import fs from 'node:fs/promises';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {gunzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {applicationTiming, percentile95, responseDiagnostics} from './market_benchmark_metrics.mjs';

export const measurementContract = {
 version:'http2-receive-end-v2',
 scope:'API endpoint load; not the browser search/detail user flow',
 phases:{first:'First batch; not guaranteed cold',warm:'Three subsequent batches; not guaranteed cache hits',after_1600ms:'Batch after a 1600ms pause; classify expiry from response headers'},
 request_headers:{'accept-encoding':'gzip',origin:null,'if-none-match':null,'cache-control':null},
 browser_behaviour_included:false,
 connection_setup_in_request_timing:false,
 request_model:'One TLS session, 100 offered concurrent streams; not 100 connections or users',
 body_validation:'ms/body_ms end at the readable end event; JSON/gzip validation runs only after the entire batch has received or failed',
 stream_readiness:'stream_ready_ms records Node stream readiness, not proven network-only or server queue time; peer_max_concurrent_streams is the observed peer limit',
 timing:'TTFB includes proxy/queue/transit; app_ms is current uncached application-header time. Do not subtract independent percentiles.',
 origin_header:'On edge HIT the origin cache header is historical, not a new origin lookup',
};

export function parseArguments(args){
 let output='work/http2-benchmark.json',connections=1,hasOutput=false,hasConnections=false;
 for(let i=0;i<args.length;i++){
  if(args[i]==='--connections'&&!hasConnections){
   hasConnections=true;const value=args[++i];
   if(!['1','100'].includes(value))throw new Error('Connections must be 1 or 100');
   connections=Number(value);
  }else if(!hasOutput&&!args[i].startsWith('-')&&!args[i].includes('://')&&args[i].endsWith('.json')){
   output=args[i];hasOutput=true;
  }else throw new Error('Usage: benchmark_market_http2.mjs [output.json] [--connections 1|100]');
 }
 return {output,connections};
}

/** One pool per host; no reconnects or HTTP/1 fallback hidden in the workload. */
export async function openHttp2Connections(authority,{
 connections=1,timeoutMs=20000,connect=http2.connect,now=()=>performance.now(),tlsOptions={},
}={}){
 if(![1,100].includes(connections))throw new Error('Connections must be 1 or 100');
 return Promise.all(Array.from({length:connections},(_,index)=>new Promise(resolve=>{
  const started=now(),entry={connection_id:index+1,session:null,connect_ms:null,
   ready_ms:null,alpn:null,peer_max_concurrent_streams:null,error:null};
  let settled=false,connected=false,settings=false;
  const finish=error=>{
   if(settled)return;settled=true;clearTimeout(timer);entry.error=error;
   entry.setup_elapsed_ms=now()-started;
   if(error)entry.session?.destroy();
   resolve(entry);
  };
  const ready=()=>{
   if(!connected||!settings)return;
   if(entry.session.alpnProtocol!=='h2'){finish('http2_not_negotiated');return;}
   entry.alpn=entry.session.alpnProtocol;entry.ready_ms=now()-started;
   entry.peer_max_concurrent_streams=entry.session.remoteSettings.maxConcurrentStreams;
   try{entry.session.setLocalWindowSize(16*1024*1024);finish(null);}
   catch{finish('connection_setup_error');}
  };
  const timer=setTimeout(()=>finish('connection_timeout'),timeoutMs);
  try{
   entry.session=connect(authority,{...tlsOptions,settings:{initialWindowSize:1024*1024}});
   entry.session.once('connect',()=>{connected=true;entry.connect_ms=now()-started;ready();});
   entry.session.once('remoteSettings',()=>{settings=true;ready();});
   entry.session.on('error',()=>finish('connection_error'));
   entry.session.once('close',()=>finish('connection_closed_before_ready'));
  }catch{finish('connection_setup_error');}
 })));
}

export async function closeHttp2Connections(pool,{timeoutMs=1000}={}){
 return Promise.all(pool.map(({session,connection_id})=>new Promise(resolve=>{
  if(!session||session.destroyed)return resolve({connection_id,closed:true,forced:false});
  let finished=false,forced=false;
  const done=()=>{if(finished)return;finished=true;clearTimeout(timer);resolve({connection_id,closed:true,forced});};
  const timer=setTimeout(()=>{forced=true;session.destroy();done();},timeoutMs);
  session.once('close',done);
  try{session.close();}catch{forced=true;session.destroy();done();}
 })));
}

/** Receive the whole batch before synchronous decoding can delay another stream. */
export async function readHttp2Batch(session,paths,{
 now=()=>performance.now(),wallNow=()=>Date.now(),timeoutMs=20000,
 maxWireBytes=2*1024*1024,maxDecodedBytes=8*1024*1024,
 decode=(body,headers)=>JSON.parse((headers['content-encoding']==='gzip'
  ?gunzipSync(body,{maxOutputLength:maxDecodedBytes}):body).toString()),
}={}) {
 if(Array.isArray(session)&&session.length!==1&&session.length!==paths.length)
  throw new Error('Each independent connection must have exactly one request per batch');
 const pending=paths.map((requestPath,index)=>new Promise(resolve=>{
  const entry=Array.isArray(session)?session[session.length===1?0:index]:{session};
  const client=entry.session;
  const start=now(),chunks=[];
  let headers={},ended=false,ttfb=null,headersAtMs=null,readyMs=null,wireBytes=0,stream;
  const peerLimit=client?.remoteSettings?.maxConcurrentStreams??null;
  const finish=(status,error=null)=>{
   if(ended)return;ended=true;clearTimeout(timer);
   const elapsed=entry.error?null:now()-start,endAtMs=wallNow();
   resolve({headers,chunks,row:{ms:elapsed,ttfb_ms:ttfb,body_ms:ttfb===null?null:elapsed-ttfb,
    wire_bytes:wireBytes,status,valid:false,request_path:requestPath,
    stream_ready_ms:readyMs,peer_max_concurrent_streams:peerLimit,
    ...(entry.connection_id?{connection_id:entry.connection_id,connection_ready_ms:entry.ready_ms}:{}),
    ...(error?{error}:{}),app_ms:applicationTiming(headers),
    origin_cache:headers['x-market-cache']||'BYPASS',edge:headers['x-market-edge']||'ORIGIN',
    ...responseDiagnostics(headers,headersAtMs,endAtMs)}});
  };
  const fail=code=>{finish(0,code);stream?.close();};
  const timer=setTimeout(()=>fail('timeout'),timeoutMs);
  if(entry.error){finish(0,entry.error);return;}
  try {
   stream=client.request({':path':requestPath,'accept-encoding':'gzip'});
   if(!stream.pending&&Number.isInteger(stream.id))readyMs=now()-start;
   stream.once('ready',()=>{if(!ended)readyMs=now()-start;});
   stream.on('response',value=>{headers=value;ttfb=now()-start;headersAtMs=wallNow();});
   stream.on('data',chunk=>{
    if(ended)return;
    wireBytes+=chunk.length;
    if(wireBytes>maxWireBytes){fail('wire_size_limit');return;}
    chunks.push(chunk);
   });
   stream.on('end',()=>finish(headers[':status']||0));
   stream.on('error',()=>finish(0,'stream_error'));
   stream.on('close',()=>finish(0,'closed_before_end'));
   stream.end();
  }catch{fail('request_error');}
 }));
 const received=await Promise.all(pending);
 return received.map(({headers,chunks,row})=>{
  if(row.error)return {...row,validation_ms:null};
  const started=now();
  try{decode(Buffer.concat(chunks),headers);row.valid=true;}
  catch{row.error='invalid_body';}
  return {...row,validation_ms:now()-started};
 });
}

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
const options=parseArguments(process.argv.slice(2));
const output=path.resolve(options.output);
const pending=output+'.partial';
await fs.mkdir(path.dirname(output),{recursive:true});
try{await fs.access(output);throw new Error('Benchmark output already exists; choose a new evidence path');}
catch(error){if(error.code!=='ENOENT')throw error;}
const hosts=(process.env.MARKET_BENCH_HOSTS||'stocks.62-171-141-206.sslip.io,korea-stocks-edge.lch68-workers.workers.dev').split(',').filter(Boolean);
const codes=['005930','000660','005380','035420','035720','051910','005490','000270','068270','105560'];
const queries=['삼성','하이닉스','현대','네이버','카카오','LG','포스코','기아','셀트리온','KB'];
const cacheProbe=process.env.MARKET_BENCH_CACHE_PROBE||'';
if(cacheProbe&&!/^[a-z0-9-]{1,48}$/.test(cacheProbe))throw new Error('Invalid cache probe identifier');
const multiple=options.connections===100;
const report={at:new Date().toISOString(),concurrency:100,connections:options.connections,cache_probe:cacheProbe||null,
 transport:multiple?'HTTP/2 100 independent TLS sessions, one concurrent request per session, Windows PC public internet':'HTTP/2 one TLS session, 100 offered concurrent streams, Windows PC public internet',
 measurement_contract:{...measurementContract,...(multiple?{
  request_model:'100 independent TLS sessions; one offered concurrent request per session; sessions reused across phases and endpoints',
 }:{}),connection_setup:'DNS/TCP/TLS/connect and peer-settings readiness recorded separately; all attempts settle before the first batch; no automatic reconnect'},
 connection_setup:[],results:[]};
// Fail before generating public load if evidence cannot be saved. Preserve a
// checkpoint after each endpoint so a later transport/disk failure loses no
// completed batch and can never be mistaken for a complete measurement.
await fs.writeFile(pending,JSON.stringify({...report,complete:false},null,2),{flag:'wx'});
for(const hostname of hosts){
 const pool=await openHttp2Connections('https://'+hostname,{connections:options.connections});
 const setup={hostname,attempted:pool.length,ready:pool.filter(e=>!e.error).length,
  samples:pool.map(({session,...entry})=>entry)};
 report.connection_setup.push(setup);
 try{
 for(const [endpoint,paths] of Object.entries({search:queries.map(q=>'/api/market/v1/search?q='+encodeURIComponent(q)),summary:codes.map(c=>'/api/market/v1/securities/'+c+'?view=summary')})){
  const batch=()=>readHttp2Batch(pool,Array.from({length:100},(_,i)=>paths[i%paths.length]+(cacheProbe?'&cache_probe='+cacheProbe:'')));
  const first=await batch(),warm=[];for(let n=0;n<3;n++)warm.push(...await batch());
  await new Promise(resolve=>setTimeout(resolve,1600));const expired=await batch();
  const row=summarizeEndpoint(hostname,endpoint,{first,warm,expired});
  report.results.push(row);const {samples,...compact}=row;console.log(JSON.stringify(compact));
  await fs.writeFile(pending,JSON.stringify({...report,complete:false},null,2));
 }
 }finally{
  setup.cleanup=await closeHttp2Connections(pool);
  await fs.writeFile(pending,JSON.stringify({...report,complete:false},null,2));
 }
}
await fs.writeFile(pending,JSON.stringify({...report,complete:true},null,2));
await fs.rename(pending,output);
}

// Importing the pure classifiers never creates evidence files or public load.
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await runBenchmark();
