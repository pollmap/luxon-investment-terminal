import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function harness(origin, respond, {controlledTimeouts=false,clock=Date}={}) {
  const calls=[],timeouts=[];
  const source=fs.readFileSync(new URL('../../components/market/public-transport.ts',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const context={exports:{},URL,Headers,Date:clock,AbortSignal:controlledTimeouts?{
    any:AbortSignal.any.bind(AbortSignal),timeout:ms=>{const controller=new AbortController();timeouts.push({ms,controller});return controller.signal;},
  }:AbortSignal,location:{origin},fetch:(url,options)=>{
    calls.push({url,options});return respond?respond(url,options,calls.length):Promise.resolve(new Response('fixture_non_production',{status:503}));
  }};
  vm.runInNewContext(compiled,context);
  const transport=context.exports;
  const loaderSource=fs.readFileSync(new URL('../../components/market/search-index.ts',import.meta.url),'utf8');
  const loader={exports:{},Date:clock,require:name=>{assert.equal(name,'./public-transport');return transport;}};
  vm.runInNewContext(ts.transpileModule(loaderSource,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,loader);
  return {...transport,...loader.exports,calls,timeouts};
}
const edge='https://korea-stocks-edge.lch68-workers.workers.dev';
const origin='https://stocks.62-171-141-206.sslip.io';

test('only the known static frontend uses direct public data; query remains intact',()=>{
  const h=harness(edge),path='/api/market/v1/search?q=%EC%82%BC%EC%84%B1';
  assert.equal(h.marketUrl(path),origin+path);
  assert.equal(h.marketUrl(path,origin),path);
  assert.equal(h.marketUrl(path,'http://localhost:8794'),path);
  assert.equal(h.marketUrl(path,'https://unrelated.example'),path);
});

test('CSV POST body, cancellation, cache option and response status survive',async()=>{
  const h=harness(edge),controller=new AbortController(),body=JSON.stringify({codes:['005930']});
  const response=await h.marketFetch('/api/market/v1/screener/export',{
    method:'POST',body,signal:controller.signal,cache:'no-store',headers:{'Content-Type':'application/json'},
  });
  assert.equal(response.status,503); // An API failure is never converted to HTML or fake data.
  assert.equal(h.calls.length,1);
  const {url,options}=h.calls[0];
  assert.equal(url,origin+'/api/market/v1/screener/export');
  assert.equal(options.body,body);assert.equal(options.signal,controller.signal);
  assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store');
  assert.equal(options.headers.get('content-type'),'application/json');
});

test('GET remains a simple read, and cache validators are preserved',async()=>{
  const h=harness(edge);
  await h.marketFetch('/api/market/v1/search-index');
  assert.equal(h.calls[0].options.headers.has('content-type'),false);
  await h.marketFetch('/api/market/v1/securities/005930',{headers:{'If-None-Match':'"fixture"'}});
  assert.equal(h.calls[1].options.headers.get('if-none-match'),'"fixture"');
});

test('credential or write attempts are rejected before any fetch',()=>{
  const h=harness(edge);
  for(const headers of [{Authorization:'fixture'},{Cookie:'fixture'},{'X-API-Key':'fixture'}]) {
    assert.throws(()=>h.marketFetch('/api/market/v1/search',{headers}));
  }
  assert.throws(()=>h.marketFetch('/api/market/v1/search',{credentials:'include'}));
  assert.throws(()=>h.marketFetch('/api/market/v1/orders',{method:'POST',body:'fixture'}));
  assert.throws(()=>h.marketFetch('/api/market/v1/screener',{method:'DELETE'}));
  assert.equal(h.calls.length,0);
});

test('absolute, internal and path-traversal addresses are not a generic proxy',()=>{
  const h=harness(edge);
  for(const path of ['https://example.com/api/market/v1/search','//example.com/x',
    '/api/market/v1/internal/state','/api/market/v1/../../../internal/state',
    '/api/market/v1/%2e%2e/%2e%2e/private','/api/market/v1/\\evil']) {
    assert.throws(()=>h.marketUrl(path));
  }
});

test('both quote and depth use the same supported direct WebSocket destination',()=>{
  const h=harness(edge);
  assert.equal(h.streamUrl(),'wss://stocks.62-171-141-206.sslip.io/api/market/v1/stream');
  assert.equal(h.streamUrl(origin),'wss://stocks.62-171-141-206.sslip.io/api/market/v1/stream');
  assert.equal(h.streamUrl('http://127.0.0.1:8794'),'ws://127.0.0.1:8794/api/market/v1/stream');
});

const indexPath='/api/market/v1/search-index';
const fixtureRows=[{code:'005930',name:'삼성전자',market:'KOSPI',tokens:['005930','삼성전자','ㅅㅅㅈㅈ']}];
const json=(body={rows:fixtureRows},headers={})=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json',...headers}});
const waitForAbort=signal=>new Promise((_,reject)=>{
  if(signal.aborted)reject(signal.reason);
  else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
});
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('only the dedicated exact index reader uses same-origin edge; ordinary paths remain direct',async()=>{
  const h=harness(edge,()=>Promise.resolve(json()));
  assert.equal((await h.loadSearchIndex())[0].code,'005930');
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,indexPath);
  assert.equal(h.calls[0].options.method,'GET');assert.equal(h.calls[0].options.credentials,'omit');
  assert.equal(h.calls[0].options.headers,undefined);
  for(const path of [indexPath+'?q=삼성',indexPath+'/',indexPath+'/extra','/api/market/v1/search?q=삼성','/api/market/v1/securities/005930?view=summary']){
    assert.equal(h.marketUrl(path),new URL(path,origin).toString());
  }
});

test('VPS, development and unrelated origins make one normal relative index request',async()=>{
  for(const host of [origin,'http://localhost:8794','https://unrelated.example']){
    const h=harness(host,()=>Promise.resolve(json()));
    await h.loadSearchIndex();assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,indexPath);
  }
});

test('403, 404, 429 and 5xx edge errors recover exactly once through the public VPS',async()=>{
  for(const status of [403,404,429,500,502,503]){
    const h=harness(edge,(_url,_options,n)=>Promise.resolve(n===1?new Response('',{status}):json()));
    assert.equal((await h.loadSearchIndex()).length,1);
    assert.deepEqual(h.calls.map(call=>call.url),[indexPath,origin+indexPath]);
    assert.equal(h.calls[1].options.credentials,'omit');
  }
});

test('network failures fallback once; two failures clear flight for an explicit new attempt',async()=>{
  const h=harness(edge,()=>Promise.reject(new TypeError('fixture network failure')));
  await assert.rejects(h.loadSearchIndex());assert.equal(h.calls.length,2);
  await assert.rejects(h.loadSearchIndex());assert.equal(h.calls.length,4);
});

test('raw 304 without a owned body and HTML/invalid JSON are never accepted as an index',async()=>{
  for(const response of [new Response(null,{status:304}),new Response('<html>fixture</html>',{headers:{'Content-Type':'text/html'}}),new Response('{',{headers:{'Content-Type':'application/json'}})]){
    const h=harness(edge,(_url,_options,n)=>Promise.resolve(n===1?response:json()));
    await h.loadSearchIndex();assert.equal(h.calls.length,2);
  }
});

test('malformed or duplicate identity rows trigger fallback instead of poisoning shared cache',async()=>{
  for(const rows of [[],[null],[{...fixtureRows[0],code:'../x'}],[{...fixtureRows[0],market:'KONEX'}],
    [{...fixtureRows[0],name:''}],[{...fixtureRows[0],tokens:[null]}],[{...fixtureRows[0],tokens:[]}],
    [fixtureRows[0],fixtureRows[0]],[{...fixtureRows[0],logo_url:{bad:true}}]]){
    const h=harness(edge,(_url,_options,n)=>Promise.resolve(json({rows:n===1?rows:fixtureRows})));
    assert.equal((await h.loadSearchIndex())[0].name,'삼성전자');assert.equal(h.calls.length,2);
  }
  const valid=harness(edge,()=>Promise.resolve(json({rows:[{...fixtureRows[0],code:'1234A0'}]})));
  assert.equal((await valid.loadSearchIndex())[0].code,'1234A0');assert.equal(valid.calls.length,1);
});

test('caller cancellation before or during the edge request never initiates fallback',async()=>{
  for(const before of [true,false]){
    const controller=new AbortController();if(before)controller.abort();
    const h=harness(edge,(_url,{signal})=>waitForAbort(signal));
    const promise=h.readSearchIndex(body=>body,controller.signal);
    if(!before)controller.abort();
    await assert.rejects(promise,{name:'AbortError'});assert.equal(h.calls.length,before?0:1);
  }
});

test('edge timeout permits only one fallback inside the original total deadline',async()=>{
  const h=harness(edge,(_url,{signal})=>waitForAbort(signal),{controlledTimeouts:true});
  const promise=h.readSearchIndex(body=>body);
  assert.deepEqual(h.timeouts.map(value=>value.ms),[8000,3000]);
  h.timeouts[1].controller.abort(new DOMException('fixture edge timeout','TimeoutError'));await tick();
  assert.equal(h.calls.length,2);assert.deepEqual(h.timeouts.map(value=>value.ms),[8000,3000,5000]);
  h.timeouts[0].controller.abort(new DOMException('fixture total deadline','TimeoutError'));
  await assert.rejects(promise,{name:'TimeoutError'});assert.equal(h.calls.length,2);
});

test('timeout continues while the body is being read, not only until response headers',async()=>{
  const h=harness(edge,(_url,{signal},n)=>{
    if(n===2)return Promise.resolve(json());
    return Promise.resolve({status:200,headers:new Headers({'Content-Type':'application/json'}),bodyUsed:true,json:()=>waitForAbort(signal)});
  },{controlledTimeouts:true});
  const promise=h.loadSearchIndex();await tick();
  h.timeouts[1].controller.abort(new DOMException('fixture body timeout','TimeoutError'));
  assert.equal((await promise)[0].code,'005930');assert.equal(h.calls.length,2);
});

test('freshness uses the shorter Date/Age budget, never a second full 300 seconds',()=>{
  const h=harness(edge),now=1_000_000;
  const headers=(date,age)=>new Headers({...date!==undefined?{Date:date}:{},...age!==undefined?{Age:age}:{}});
  assert.equal(h.searchIndexExpires(headers(new Date(now-280000).toUTCString(),'290'),now,now),now+10000);
  assert.equal(h.searchIndexExpires(headers(new Date(now-295000).toUTCString(),'100'),now,now),now+5000);
  for(const value of [headers(),headers('bad','0'),headers(new Date(now+1000).toUTCString(),'0'),headers(new Date(now).toUTCString(),'bad')])assert.equal(h.searchIndexExpires(value,now,now),0);
  assert.ok(h.searchIndexExpires(headers(new Date(now-400000).toUTCString(),'400'),now,now)<=now);
  assert.equal(h.searchIndexExpires(new Headers({Date:new Date(now).toUTCString(),'Cache-Control':'private, max-age=300'}),now,now),0);
});

test('concurrent consumers share a flight and memory expires at the upstream remaining age',async()=>{
  let now=1_000_000;
  class Clock extends Date {static now(){return now;}}
  let complete;
  const h=harness(edge,(_url,_options,n)=>n===1?new Promise(resolve=>{complete=resolve;}):Promise.resolve(json()),{clock:Clock});
  const first=h.loadSearchIndex(),second=h.loadSearchIndex();assert.equal(first,second);assert.equal(h.calls.length,1);
  complete(json(undefined,{Date:new Date(now).toUTCString(),Age:'299'}));
  await first;now+=999;await h.loadSearchIndex();assert.equal(h.calls.length,1);
  now+=2;await h.loadSearchIndex();assert.equal(h.calls.length,2);
});

test('hidden upstream age permits a valid body but no invented memory freshness',async()=>{
  const h=harness(edge,()=>Promise.resolve(json()));
  await h.loadSearchIndex();await h.loadSearchIndex();assert.equal(h.calls.length,2);
});
