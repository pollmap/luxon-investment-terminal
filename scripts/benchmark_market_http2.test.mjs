import test from 'node:test';
import assert from 'node:assert/strict';
import http2 from 'node:http2';
import fs from 'node:fs/promises';
import {EventEmitter} from 'node:events';
import {gzipSync} from 'node:zlib';

let networkCalls=0,fileCalls=0;
const connect=http2.connect,mkdir=fs.mkdir;
http2.connect=()=>{networkCalls++;throw new Error('No network permitted in classification tests');};
fs.mkdir=()=>{fileCalls++;throw new Error('Import must not create benchmark evidence');};
let classifiers;
try {classifiers=await import('./benchmark_market_http2.mjs');}
finally {http2.connect=connect;fs.mkdir=mkdir;}
const {classifyCache,measurementContract,summarizeEndpoint,summarizeRows,readHttp2Batch}=classifiers;
const sample=(ms,edge='ORIGIN',origin_cache='MISS',extra={})=>({
 fixture_non_production:true,ms,ttfb_ms:ms-2,body_ms:2,wire_bytes:100,
 status:200,valid:true,app_ms:null,edge,origin_cache,...extra,
});

test('pure import cannot start HTTP2 or create benchmark output',()=>{
 assert.equal(networkCalls,0);assert.equal(fileCalls,0);
});

test('legacy summary fields and percentile convention remain compatible',()=>{
 assert.deepEqual(summarizeRows([sample(10),sample(20,'ORIGIN','HIT'),sample(40,'ORIGIN','EXPIRED',{app_ms:3})]),{
  requests:3,ttfb_p95_ms:38,body_p95_ms:2,wire_bytes:300,
  origin_cache:{EXPIRED:1,HIT:1,MISS:1},p50_ms:20,p95_ms:40,
  app_timing_samples:1,app_p95_ms:3,errors:0,cache:{ORIGIN:3},
 });
});

test('repeated phase is not relabelled as HIT; actual cache groups stay separate',()=>{
 const rows=[sample(10,'HIT','EXPIRED'),sample(80,'MISS','EXPIRED'),sample(100,'MISS','MISS')];
 const result=summarizeEndpoint('fixture.invalid','summary',{first:[],warm:rows,expired:[]});
 assert.equal(result.warm.requests,3);assert.equal(result.warm.p95_ms,100);
 const groups=result.warm.cache_breakdown;
 assert.deepEqual(groups.by_edge.map(row=>[row.states.edge,row.requests,row.p95_ms]),[['HIT',1,10],['MISS',2,100]]);
 assert.deepEqual(groups.by_origin_header.map(row=>[row.states.origin_cache,row.requests]),[['EXPIRED',2],['MISS',1]]);
 assert.equal(groups.by_edge_and_origin.length,3);
 for(const classification of Object.values(groups))assert.equal(classification.reduce((sum,row)=>sum+row.requests,0),3);
 assert.equal(result.samples.warm,rows);
});

test('edge HIT retains historical origin status without inventing new app measurements',()=>{
 const [result]=classifyCache([sample(12,'HIT','EXPIRED')]).by_edge_and_origin;
 assert.equal(result.states.edge,'HIT');assert.equal(result.states.origin_cache,'EXPIRED');
 assert.equal(result.app_timing_samples,0);assert.equal(result.app_p95_ms,null);
 assert.match(measurementContract.origin_header,/historical/);
});

test('timeouts and missing headers stay errors rather than HIT or zero latency',()=>{
 const groups=classifyCache([{fixture_non_production:true,ms:20000,status:0,request_path:'/api/market/v1/search?q=x'}]);
 const [result]=groups.by_edge_and_origin;
 assert.equal(result.states.edge,'ERROR');assert.equal(result.states.origin_cache,'ERROR');
 assert.equal(result.errors,1);assert.equal(result.ttfb_p95_ms,null);assert.equal(result.app_p95_ms,null);
 assert.equal(summarizeRows([]).p95_ms,null);
});

test('request paths survive classification and historical missing paths stay missing',()=>{
 const rows=Object.freeze([
  Object.freeze(sample(1,'ORIGIN','MISS',{request_path:'/api/market/v1/search?q=%EC%82%BC%EC%84%B1'})),
  Object.freeze(sample(2,'ORIGIN','HIT')),
 ]);
 const before=JSON.stringify(rows),result=summarizeEndpoint('fixture.invalid','search',{first:rows,warm:[],expired:[]});
 assert.equal(result.samples.first[0].request_path,rows[0].request_path);
 assert.equal(Object.hasOwn(result.samples.first[1],'request_path'),false);
 assert.equal(JSON.stringify(rows),before);
});

test('phase and workload contract do not claim browser flow or cold/cache-hit guarantees',()=>{
 assert.equal(measurementContract.browser_behaviour_included,false);
 assert.equal(measurementContract.connection_setup_in_request_timing,false);
 assert.equal(measurementContract.request_headers.origin,null);
 assert.equal(measurementContract.request_headers['if-none-match'],null);
 assert.match(measurementContract.phases.first,/not guaranteed cold/);
 assert.match(measurementContract.phases.warm,/not guaranteed cache hits/);
});

test('independent timing distributions are reported without subtracting percentiles',()=>{
 const summary=summarizeRows([sample(100,'ORIGIN','MISS',{app_ms:90}),sample(120,'ORIGIN','MISS',{app_ms:10})]);
 assert.equal(summary.p95_ms,120);assert.equal(summary.app_p95_ms,90);
 assert.equal(Object.hasOwn(summary,'network_p95_ms'),false);
 assert.match(measurementContract.timing,/Do not subtract independent percentiles/);
});

class FixtureStream extends EventEmitter {
 pending=true;
 close(){this.closed=true;this.emit('close');}
 end(){}
}
function fixtureSession(limit=100){
 return {streams:[],remoteSettings:{maxConcurrentStreams:limit},request(headers){
  const stream=new FixtureStream();stream.requestHeaders=headers;this.streams.push(stream);return stream;
 }};
}
function complete(stream,body='{}',headers={}){
 stream.emit('response',{':status':200,...headers});
 stream.emit('data',Buffer.from(body));stream.emit('end');stream.emit('close');
}

test('all receive-end clocks are captured before any synchronous validation',async()=>{
 const session=fixtureSession(40);let clock=0,decodes=0;
 const pending=readHttp2Batch(session,['/one','/two'],{now:()=>clock,wallNow:()=>10000+clock,
  decode(body){decodes++;JSON.parse(body.toString());clock+=100;}});
 clock=3;session.streams[0].emit('ready');
 clock=10;complete(session.streams[0],'{}',{'x-market-validated-at':'10000'});
 await Promise.resolve();assert.equal(decodes,0);
 clock=20;complete(session.streams[1],'{}',{'x-market-validated-at':'10000'});
 const rows=await pending;
 assert.deepEqual(rows.map(row=>row.ms),[10,20]);
 assert.deepEqual(rows.map(row=>row.validation_ms),[100,100]);
 assert.deepEqual(rows.map(row=>row.freshness.end_at_ms),[10010,10020]);
 assert.equal(rows[0].stream_ready_ms,3);assert.equal(rows[1].stream_ready_ms,null);
 assert(rows.every(row=>row.peer_max_concurrent_streams===40&&row.valid));
 assert(!JSON.stringify(rows).includes('chunks'));
 assert.match(measurementContract.request_model,/not 100 connections/);
});

test('100 offered streams remain one session and preserve each observed peer limit',async()=>{
 const session=fixtureSession(25);
 const pending=readHttp2Batch(session,Array.from({length:100},(_,i)=>'/fixture/'+i));
 assert.equal(session.streams.length,100);
 for(const stream of session.streams)complete(stream,'{"fixture_non_production":true}');
 const rows=await pending;
 assert.equal(rows.length,100);assert(rows.every(row=>row.valid&&row.peer_max_concurrent_streams===25));
 assert(session.streams.every(stream=>stream.requestHeaders['accept-encoding']==='gzip'));
});

test('gzip and JSON failures are errors; validation cannot change measured transport time',async()=>{
 const session=fixtureSession();let clock=0;
 const pending=readHttp2Batch(session,['/gzip','/bad-json','/bad-gzip'],{now:()=>clock});
 clock=10;complete(session.streams[0],gzipSync('{"ok":true}'),{'content-encoding':'gzip'});
 clock=20;complete(session.streams[1],'not JSON');
 clock=30;complete(session.streams[2],'not gzip',{'content-encoding':'gzip'});
 const rows=await pending;
 assert.deepEqual(rows.map(row=>row.ms),[10,20,30]);
 assert.deepEqual(rows.map(row=>row.valid),[true,false,false]);
 assert.equal(rows[1].error,'invalid_body');assert.equal(rows[2].error,'invalid_body');
 assert.equal(summarizeRows(rows).errors,2);
});

test('wire and decoded size limits fail without treating a partial response as success',async()=>{
 const wire=fixtureSession();
 const pending=readHttp2Batch(wire,['/oversize'],{maxWireBytes:2});
 complete(wire.streams[0],'123');
 const [tooLarge]=await pending;
 assert.equal(tooLarge.error,'wire_size_limit');assert.equal(tooLarge.valid,false);
 assert.equal(wire.streams[0].closed,true);assert.equal(tooLarge.validation_ms,null);
 const decoded=fixtureSession();
 const gzipped=readHttp2Batch(decoded,['/oversize-gzip'],{maxDecodedBytes:8});
 complete(decoded.streams[0],gzipSync(JSON.stringify('x'.repeat(100))),{'content-encoding':'gzip'});
 const [expanded]=await gzipped;
 assert.equal(expanded.error,'invalid_body');assert.equal(expanded.valid,false);
});

test('early close, synchronous request failure and timeout settle once as errors',async()=>{
 const closed=fixtureSession();
 const first=readHttp2Batch(closed,['/closed']);closed.streams[0].close();
 assert.equal((await first)[0].error,'closed_before_end');
 const [rejected]=await readHttp2Batch({request(){throw new Error('fixture');}},['/reject']);
 assert.equal(rejected.error,'request_error');
 const stalled=fixtureSession();
 const timeout=readHttp2Batch(stalled,['/stalled'],{timeoutMs:5});
 const [timedOut]=await timeout;
 complete(stalled.streams[0]);
 assert.equal(timedOut.error,'timeout');assert.equal(timedOut.status,0);
 assert.equal(timedOut.valid,false);assert.equal(stalled.streams[0].closed,true);
});

test('historical Server-Timing remains excluded after deferred body validation',async()=>{
 const session=fixtureSession();
 const pending=readHttp2Batch(session,['/hit','/miss']);
 complete(session.streams[0],'{}',{'x-market-cache':'HIT','server-timing':'app;dur=1000'});
 complete(session.streams[1],'{}',{'x-market-cache':'MISS','server-timing':'app;dur=20'});
 const rows=await pending;
 assert.deepEqual(rows.map(row=>row.app_ms),[null,20]);
 assert.deepEqual(rows.map(row=>row.timing_is_current),[false,true]);
 assert.equal(rows[0].raw_headers['server-timing'],'app;dur=1000');
});
