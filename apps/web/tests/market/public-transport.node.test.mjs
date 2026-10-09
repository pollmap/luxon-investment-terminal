import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function harness(origin) {
  const calls=[];
  const source=fs.readFileSync(new URL('../../components/market/public-transport.ts',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const context={exports:{},URL,Headers,location:{origin},fetch:(url,options)=>{
    calls.push({url,options});return Promise.resolve(new Response('fixture_non_production',{status:503}));
  }};
  vm.runInNewContext(compiled,context);
  return {...context.exports,calls};
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
