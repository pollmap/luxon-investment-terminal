import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker,{policy,deadline} from './worker.mjs';
import {legacyDestination} from './legacy-routes.mjs';
import {readFile} from 'node:fs/promises';
const url=path=>new URL('https://fixture.test'+path);
test('only explicitly public stable paths and bounded quotes cache',()=>{
 assert.equal(policy(url('/api/market/v1/search-index')).seconds,300);
 assert.equal(policy(url('/api/market/v1/search?q=005930')).seconds,1);
 for(const path of ['/api/market/v1/securities/005930/depth','/api/market/v1/securities/005930/bars','/api/market/v1/health','/internal/quotes','/api/market/v1/securities/005930/analysis/valuation'])assert.equal(policy(url(path)),null);
});

test('private, unsupported Vary and stable no-store responses never become shared entries',()=>{
 for(const headers of [{'Cache-Control':'private, max-age=600'}, {'Cache-Control':'no-store'},
   {'Vary':'*'}, {'Vary':'Cookie'}, {'Vary':'Accept-Language'}, {'Age':'invalid'}]){
   assert.equal(deadline(new Response('{}',{headers}),{seconds:300,live:false},1000,1000),0);
 }
 assert.equal(deadline(new Response('{}',{headers:{'Cache-Control':'private','x-market-validated-at':'1000'}}),{seconds:1,live:true},1000,1000),0);
});

test('Vary Origin requires an explicitly partitioned key; other Vary stays rejected',()=>{
 const response=new Response('{}',{headers:{Vary:'Accept-Encoding, Origin'}});
 assert.equal(deadline(response,{seconds:300,live:false},1000,1000),0);
 assert.equal(deadline(response,{seconds:300,live:false},1000,1000,true),301000);
 for(const vary of ['Origin, Cookie','Origin, Authorization','*'])
  assert.equal(deadline(new Response('{}',{headers:{Vary:vary}}),{seconds:300,live:false},1000,1000,true),0);
});

test('stable upstream Age and Date reduce the remaining edge lifetime',()=>{
 assert.equal(deadline(new Response('{}',{headers:{Age:'290'}}),{seconds:300,live:false},500000,500000),510000);
 assert.equal(deadline(new Response('{}',{headers:{Date:new Date(100000).toUTCString()}}),{seconds:300,live:false},200000,200000),400000);
 assert.ok(deadline(new Response('{}',{headers:{Age:'301'}}),{seconds:300,live:false},500000,500000)<500000);
});

test('HTTP 200 does not establish that financial periods are confirmed',()=>{
 const rules=policy(url('/api/market/v1/securities/005930/financials/ttm'));
 assert.equal(deadline(new Response('{"status":"period_evidence_required"}'),rules,1000,1000),0);
 assert.equal(deadline(new Response('{}',{headers:{'X-Market-Cache-State':'confirmed'}}),rules,1000,1000),301000);
});

async function withRuntime(run,{match,put,upstream,assets}={}){
 const originalFetch=globalThis.fetch,originalCaches=globalThis.caches,stored=new Map(),calls=[],pending=[];
 globalThis.caches={default:{match:match||((key)=>stored.get(key.url)?.clone()),
   put:put||((key,response)=>{stored.set(key.url,response.clone());})}};
 globalThis.fetch=async(request,options)=>{calls.push({request,options});return upstream?upstream(request,options):new Response('{"rows":[]}',{headers:{'Content-Type':'application/json',ETag:'"sample"','Cache-Control':'public, max-age=300'}});};
 const env={ORIGIN:'https://origin.example.test',RELEASE:'test-release'};
 if(assets)env.ASSETS={fetch:assets};
 const request=(path,headers,init={})=>worker.fetch(new Request('https://edge.example.test'+path,{...init,headers}),env,{waitUntil:p=>pending.push(p)});
 const drain=()=>Promise.all(pending);
 try{return await run({request,calls,stored,drain});}
 finally{await drain();globalThis.fetch=originalFetch;globalThis.caches=originalCaches;}
}

const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const liveHeaders=validated=>({'Content-Type':'application/json','x-market-validated-at':String(validated),
  'Cache-Control':'no-store','Server-Timing':'app;dur=12','X-Market-Cache':'MISS'});

test('overlapping live misses share only bytes and preserve independent response bodies',async()=>{
 const gate=deferred(),entered=deferred(),validated=Date.now();
 await withRuntime(async({request,calls,drain})=>{
  const first=request('/api/market/v1/search?q=fixture');await entered.promise;
  const second=request('/api/market/v1/search?q=fixture');
  await Promise.resolve();gate.resolve();
  const responses=await Promise.all([first,second]);
  assert.equal(calls.length,1);
  assert.deepEqual(await responses[0].json(),{rows:[{code:'005930'}]});
  assert.deepEqual(await responses[1].clone().json(),{rows:[{code:'005930'}]});
  assert.deepEqual(await responses[1].json(),{rows:[{code:'005930'}]});
  assert.equal(responses[1].headers.get('x-market-coalesced'),'1');
  assert.equal(responses[1].headers.get('x-market-validated-at'),String(validated));
  assert.equal(responses[1].headers.get('cache-control'),'no-store');await drain();
 },{match:()=>null,upstream:async()=>{
  entered.resolve();await gate.promise;
  return new Response('{"rows":[{"code":"005930"}]}',{headers:liveHeaders(validated)});
 }});
});

test('expired source clock, errors and private responses are not shared',async()=>{
 for(const kind of ['expired','404','private']){
  const gate=deferred(),entered=deferred(),validated=Date.now()-(kind==='expired'?2000:0);
  await withRuntime(async({request,calls,stored,drain})=>{
   const first=request('/api/market/v1/search?q='+kind);await entered.promise;
   const second=request('/api/market/v1/search?q='+kind);await Promise.resolve();gate.resolve();
   const responses=await Promise.all([first,second]);
   for(const response of responses){assert.equal(response.headers.get('x-market-coalesced'),null);await response.text();}
   await drain();assert.equal(calls.length,2);assert.equal(stored.size,0);
  },{upstream:async()=>{entered.resolve();await gate.promise;
   return new Response('{}',{status:kind==='404'?404:200,headers:{...liveHeaders(validated),
    ...(kind==='private'?{'Cache-Control':'private, no-store'}:{})}});
  }});
 }
});

test('source deadline exhausted while the body arrives cannot be shared or cached',async()=>{
 const originalNow=Date.now;let clock=10000;Date.now=()=>clock;
 const bodyGate=deferred(),entered=deferred();let requests=0;
 try{await withRuntime(async({request,calls,stored,drain})=>{
  const first=request('/api/market/v1/search?q=body-deadline');await entered.promise;
  const second=request('/api/market/v1/search?q=body-deadline');await Promise.resolve();
  clock=11001;bodyGate.resolve();const responses=await Promise.all([first,second]);
  await Promise.all(responses.map(response=>response.text()));await drain();
  assert.equal(calls.length,2);assert.equal(stored.size,0);
  assert(responses.every(response=>response.headers.get('x-market-validated-at')==='10000'));
 },{upstream:()=>{
  requests++;entered.resolve();
  return new Response(requests===1?new ReadableStream({async start(controller){
   await bodyGate.promise;controller.enqueue(new TextEncoder().encode('{}'));controller.close();
  }}):'{}',{headers:liveHeaders(10000)});
 }});}finally{Date.now=originalNow;}
});

test('query, Origin and conditional representation variants cannot share a pending result',async()=>{
 const gate=deferred();
 await withRuntime(async({request,calls,drain})=>{
  const responses=[
   request('/api/market/v1/search?q=a&custom=1'),request('/api/market/v1/search?q=a&custom=2'),
   request('/api/market/v1/search?q=a&custom=1',{Origin:'https://different.test'}),
   request('/api/market/v1/search?q=a&custom=1',{'If-None-Match':'"other"'}),
   request('/api/market/v1/search?q=a&custom=1',{'If-Match':'"other"'}),
   request('/api/market/v1/search?q=a&custom=1',{'If-Unmodified-Since':'Thu, 08 Oct 2026 00:00:00 GMT'}),
  ];
  await Promise.resolve();gate.resolve();
  await Promise.all((await Promise.all(responses)).map(response=>response.text()));await drain();
  assert.equal(calls.length,6);
 },{match:()=>null,upstream:async()=>{await gate.promise;return new Response('{}',{headers:liveHeaders(Date.now())});}});
});

test('oversized live bodies retain the original body and bypass sharing',async()=>{
 const gate=deferred(),entered=deferred();const body=JSON.stringify('x'.repeat(65536));
 await withRuntime(async({request,calls,drain})=>{
  const first=request('/api/market/v1/search?q=large');await entered.promise;
  const second=request('/api/market/v1/search?q=large');await Promise.resolve();gate.resolve();
  const responses=await Promise.all([first,second]);
  for(const response of responses){assert.equal(await response.text(),body);assert.equal(response.headers.get('x-market-coalesced'),null);}
  await drain();assert.equal(calls.length,2);
 },{match:()=>null,upstream:async()=>{entered.resolve();await gate.promise;return new Response(body,{headers:liveHeaders(Date.now())});}});
});

test('rejected owner fetch releases the pending entry and waiters retry independently',async()=>{
 const gate=deferred(),entered=deferred();let attempts=0;
 await withRuntime(async({request,calls,drain})=>{
  const first=request('/api/market/v1/search?q=retry');await entered.promise;
  const second=request('/api/market/v1/search?q=retry');await Promise.resolve();gate.resolve();
  const [failed,recovered]=await Promise.all([first,second]);
  assert.equal(failed.status,502);assert.equal(recovered.status,200);
  await failed.text();await recovered.text();await drain();assert.equal(calls.length,2);
  await (await request('/api/market/v1/search?q=retry')).text();assert.equal(calls.length,3);
 },{match:()=>null,upstream:async()=>{if(++attempts===1){entered.resolve();await gate.promise;throw new Error('fixture abort');}
  return new Response('{}',{headers:liveHeaders(Date.now())});}});
});

test('cache hit supports ETag without renewing the browser freshness budget',async()=>{
 await withRuntime(async({request,calls,drain})=>{
  const first=await request('/api/market/v1/search-index');await drain();
  assert.equal(first.headers.get('x-market-edge'),'MISS');
  const second=await request('/api/market/v1/search-index',{'If-None-Match':'W/"sample"'});
  assert.equal(second.status,304);
  assert.equal(second.headers.get('x-market-edge'),'HIT');
  assert.equal(second.headers.get('cache-control'),'public, max-age=0, must-revalidate');
  assert.equal(second.headers.get('x-edge-expires'),null);
  assert.equal(await second.text(),'');assert.equal(calls.length,1);
 });
});

test('CORS cache prime order preserves each Origin response and cannot be forged in query',async()=>{
 const edge='https://korea-stocks-edge.lch68-workers.workers.dev';
 for(const origins of [[undefined,edge,'https://unapproved.test','null'],[edge,undefined,'null','https://unapproved.test']]){
  await withRuntime(async({request,calls,drain,stored})=>{
   for(const origin of origins){
    const headers=origin===undefined?{}:{Origin:origin};
    const path='/api/market/v1/search-index?__request_origin=forged';
    const first=await request(path,headers);await drain();
    assert.equal(first.headers.get('x-market-edge'),'MISS');
    const second=await request(path,headers);
    assert.equal(second.headers.get('x-market-edge'),'HIT');
    assert.equal(second.headers.get('access-control-allow-origin'),origin===edge?edge:null);
    assert.equal(second.headers.get('vary'),'Accept-Encoding, Origin');
    assert.deepEqual(await second.json(),{origin:origin??null});
   }
   assert.equal(calls.length,4);assert.equal(stored.size,4);
   assert.ok([...stored.keys()].every(key=>new URL(key).searchParams.get('__request_origin')!=='forged'));
  },{upstream:(_request,options)=>{
   const origin=options.headers.get('origin');
   return new Response(JSON.stringify({origin}),{headers:{
    'Cache-Control':'public, max-age=300',Vary:'Accept-Encoding, Origin',
    ...(origin===edge?{'Access-Control-Allow-Origin':edge}:{})
   }});
  }});
 }
});

test('live CORS cache keeps the original validation clock and one-second deadline',async()=>{
 const validated=Date.now()-200;
 await withRuntime(async({request,drain,stored,calls})=>{
  const headers={Origin:'https://edge.example.test'};
  const path='/api/market/v1/securities/005930?view=summary';
  await request(path,headers);await drain();
  const second=await request(path,headers);
  assert.equal(second.headers.get('x-market-edge'),'HIT');
  assert.equal(second.headers.get('x-market-validated-at'),String(validated));
  assert.equal(second.headers.get('cache-control'),'no-store');
  assert.equal(Number([...stored.values()][0].headers.get('x-edge-expires')),validated+1000);
  assert.equal(calls.length,1);
 },{upstream:()=>new Response('{}',{headers:{Vary:'Origin','x-market-validated-at':String(validated)}})});
});

test('static documents and assets stay on the asset binding without calling VPS',async()=>{
 const seen=[];
 await withRuntime(async({request,calls})=>{
  for(const path of ['/stocks?code=005930&kind=candle','/_next/static/chunks/abc.js','/stocks/sw.js','/brand/jusikdam-symbol.png']){
   const response=await request(path);
   assert.equal(response.status,200);
  }
  assert.equal(calls.length,0);assert.equal(seen.length,4);
 },{assets:async(request)=>{seen.push(request.url);return new Response('asset');}});
});

test('legacy direct links preserve stock, tab, period, filters and query parameters',()=>{
 const cases=[
  ['/?code=005930&kind=candle','005930',null,null],
  ['/company/005930.KS/financials?period=year&filters=%7B%7D','005930','재무·지표','financials'],
  ['/company/000660/consensus','000660','기업·주주','estimates'],
  ['/company/005930/graph','005930','기업·주주','valuation'],
  ['/terminal?ticker=005930.KS&tab=Historical&period=5y','005930','기업·주주','valuation'],
 ];
 for(const [input,code,tab,analysis] of cases){
  const target=new URL(legacyDestination(input),'https://fixture.test');
  assert.equal(target.pathname,'/stocks');assert.equal(target.searchParams.get('code'),code);
  assert.equal(target.searchParams.get('tab'),tab);assert.equal(target.searchParams.get('analysis'),analysis);
  const original=new URL(input,'https://fixture.test');
  for(const key of ['kind','period','filters'])assert.equal(target.searchParams.get(key),original.searchParams.get(key));
 }
 assert.equal(new URL(legacyDestination('/screener'),'https://fixture.test').searchParams.get('page'),'screener');
 assert.equal(new URL(legacyDestination('/portfolio'),'https://fixture.test').searchParams.get('page'),'watchlist');
 assert.equal(new URL(legacyDestination('/terminal?tab=Portfolio'),'https://fixture.test').searchParams.get('page'),'watchlist');
 assert.equal(legacyDestination('/company/AAPL/graph'),null);
 assert.equal(legacyDestination('/stocks?code=005930'),null);
 assert.equal(legacyDestination('/external/page'),null);
});

test('legacy Worker redirects are same-origin and do not fetch the old server',async()=>{
 await withRuntime(async({request,calls})=>{
  for(const path of ['/','/terminal','/screener','/portfolio','/system','/company/005930.KS','/company/005930/financials']){
   const response=await request(path);assert.equal(response.status,302);
   assert.ok(response.headers.get('location').startsWith('/stocks'));
   assert.equal(response.headers.get('cache-control'),'no-store');
  }
  assert.equal(calls.length,0);
 });
});

test('static profile forwards CSV and calculation POST bodies without caching',async()=>{
 for(const path of ['/api/market/v1/screener/export','/api/market/v1/screener','/api/market/v1/analysis/scenario']){
  await withRuntime(async({request,calls,stored,drain})=>{
   const response=await request(path,{'Content-Type':'application/json'},{method:'POST',body:'{"codes":["005930"]}'});
   assert.equal(response.status,200);assert.equal(calls.length,1);
   assert.equal(calls[0].request.method,'POST');
   assert.equal(await calls[0].request.text(),'{"codes":["005930"]}');
   assert.equal(new URL(calls[0].request.url).hostname,'origin.example.test');
   await drain();assert.equal(stored.size,0);
  },{assets:()=>{throw new Error('API must not use assets');}});
 }
});

test('all market data API families keep their path and query at the fixed origin',async()=>{
 const paths=['search?q=삼성','search-index','overview','listings','rankings?type=volume','market-context','sectors','sectors/catalogue','sectors/rankings','sectors/semiconductor','sectors/semiconductor/stocks','themes','themes/1','compare?codes=005930,000660','news','market-brief?date=2026-10-08','indices/KOSPI/chart?period=5y','indices/KOSPI/investors?period=day','analysis/metrics',
  ...['','quote','chart','bars','prices','price-window','depth','trading','trading/history','short-selling','financing','warnings','financials','financials/per-share','financials/timeline','financials/ttm','analysis','analysis/metrics','analysis/valuation','analysis/price-context','events','peers','news','relations'].map(part=>'securities/005930'+(part?'/'+part:''))];
 await withRuntime(async({request,calls})=>{
  for(const suffix of paths){
   const path='/api/market/v1/'+suffix;
   assert.equal((await request(path)).status,200);
   const upstream=new URL(calls.at(-1).request.url),input=new URL(path,'https://fixture.test');
   assert.equal(upstream.pathname,input.pathname);assert.equal(upstream.search,input.search);
  }
 },{assets:()=>{throw new Error('API must not use assets');}});
});

test('WebSocket handshake, query and upstream response survive static profile',async()=>{
 const upgraded={status:101};
 await withRuntime(async({request,calls})=>{
  assert.equal(await request('/api/market/v1/stream?codes=005930',{'Upgrade':'websocket','Sec-WebSocket-Protocol':'fixture'}),upgraded);
  assert.equal(calls[0].options.headers.get('upgrade'),'websocket');
  assert.equal(calls[0].options.headers.get('sec-websocket-protocol'),'fixture');
  assert.equal(new URL(calls[0].request.url).search,'?codes=005930');
 },{assets:()=>{throw new Error('not asset');},upstream:()=>upgraded});
});

test('unknown assets and API errors never become a successful HTML SPA fallback',async()=>{
 await withRuntime(async({request,calls})=>{
  assert.equal((await request('/missing.js')).status,404);
  for(const path of ['/api/auth/session','/api/market/v1/account','/api/market/v1/internal','/api/pf/companies','/internal/health','/ops/logs'])
   assert.equal((await request(path)).status,404);
  assert.equal(calls.length,0);
  assert.equal((await request('/api/market/v1/search',{}, {method:'DELETE'})).status,405);
  assert.equal((await request('/api/market/v1/order',{}, {method:'POST',body:'{}'})).status,405);
  assert.equal(calls.length,0);
 },{assets:()=>new Response('missing',{status:404})});
});

test('static routing invokes Worker only for API, internal checks and legacy links',async()=>{
 const config=JSON.parse(await readFile(new URL('./wrangler.jsonc',import.meta.url),'utf8'));
 assert.equal(config.assets.binding,'ASSETS');assert.equal(config.assets.not_found_handling,'404-page');
 assert.ok(Array.isArray(config.assets.run_worker_first));
 assert.ok(config.assets.run_worker_first.includes('/api/*'));
 assert.ok(!config.assets.run_worker_first.includes('/*'));
 assert.ok(!config.assets.run_worker_first.includes('/stocks*'));
 assert.ok(!config.assets.run_worker_first.includes('/_next/*'));
});

test('cache read and write failures cannot break successful origin responses',async()=>{
 for(const failure of ['match','put'])await withRuntime(async({request,drain})=>{
  const response=await request('/api/market/v1/search-index');await drain();
  assert.equal(response.status,200);assert.deepEqual(await response.json(),{rows:[]});
 },{[failure]:()=>{throw new Error('cache unavailable');}});
 await withRuntime(async({request,drain})=>{
  assert.equal((await request('/api/market/v1/search-index')).status,200);await drain();
 },{put:()=>Promise.reject(new Error('quota'))});
});

test('errors are not stored or cached by downstream clients',async()=>{
 for(const status of [404,429,500])await withRuntime(async({request,stored,drain,calls})=>{
  const response=await request('/_next/static/chunks/fixture-123.js');await drain();
  assert.equal(response.status,status);assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(stored.size,0);assert.equal(calls[0].options.cache,'no-store');
 },{upstream:()=>new Response('error',{status,headers:{'Cache-Control':'public, max-age=600'}})});
});

test('authorization, cookie, ranges, and explicit revalidation bypass shared cache',async()=>{
 for(const headers of [{Authorization:'Bearer fixture'}, {'X-Api-Key':'fixture'}, {'Proxy-Authorization':'fixture'}, {Cookie:'fixture=yes'}, {Range:'bytes=0-10'},
   {'Cache-Control':'no-cache'}, {'Cache-Control':'max-age=0'}, {Upgrade:'WebSocket'}]){
  await withRuntime(async({request,stored,drain})=>{
   const response=await request('/api/market/v1/search-index',headers);await drain();
   assert.equal(response.headers.get('x-market-edge'),'BYPASS');assert.equal(stored.size,0);
  },{match:()=>{throw new Error('must not read shared cache');}});
 }
});

test('private origin directive is preserved rather than rewritten as public',async()=>{
 await withRuntime(async({request,stored,drain})=>{
  const response=await request('/api/market/v1/search-index');await drain();
  assert.equal(response.headers.get('cache-control'),'private, max-age=0');assert.equal(stored.size,0);
 },{upstream:()=>new Response('{}',{headers:{'Cache-Control':'private, max-age=0'}})});
});

test('expired quote entries cannot be revived by a newer edge receipt',async()=>{
 await withRuntime(async({request,stored,drain})=>{
  const response=await request('/api/market/v1/search?q=005930');await drain();
  assert.equal(stored.size,0);assert.equal(response.headers.get('cache-control'),'no-store');
 },{upstream:()=>new Response('{}',{headers:{'x-market-validated-at':String(Date.now()-2000)}})});
});

test('blocked internal paths do not call the origin and cannot be cached',async()=>{
 await withRuntime(async({request,calls,stored})=>{
  for(const path of ['/internal/quotes','/ops/status']){
   const response=await request(path);
   assert.equal(response.status,404);assert.equal(response.headers.get('cache-control'),'no-store');
  }
  assert.equal(calls.length,0);assert.equal(stored.size,0);
 });
});

test('evidence search only exposes explicit reads and never uses shared cache',async()=>{
 await withRuntime(async({request,calls,stored})=>{
  for(const path of ['/api/market/v1/research/index','/api/market/v1/research/embeddings','/api/market/v1/research/documents/invalid']){
   const response=await request(path);assert.equal(response.status,404);
  }
  assert.equal(calls.length,0);
  for(const path of ['/api/market/v1/research/search?q=test','/api/market/v1/research/documents/'+'a'.repeat(64)]){
   const response=await request(path);assert.equal(response.status,200);
   assert.equal(response.headers.get('x-market-edge'),'BYPASS');
  }
  assert.equal(calls.length,2);assert.equal(stored.size,0);
 });
});
test('upstream cache age is part of the one-second budget; errors never cache',()=>{
 const rules={seconds:1,live:true};
 assert.equal(deadline(new Response('{}',{headers:{'x-market-validated-at':'1000'}}),rules,1700,1800),2000);
 assert.ok(deadline(new Response('{}',{headers:{'x-market-validated-at':'1000'}}),rules,2000,2400)<2400);
 assert.equal(deadline(new Response('{}'),rules,1000,1000),0);
 assert.equal(deadline(new Response('{}',{status:404}),{seconds:300,live:false},1000),0);
 assert.equal(deadline(new Response('{}',{headers:{'set-cookie':'x=y'}}),{seconds:300,live:false},1000),0);
});

test('conservative Nginx expiry does not restart the millisecond edge budget',()=>{
 const rules={seconds:1,live:true};
 for(const offset of [0,1,999]){
  const validated=1791558604000+offset;
  const response=new Response('{}',{headers:{
   'cache-control':'no-store',
   'x-market-validated-at':String(validated),
   'x-accel-expires':'@'+Math.floor(validated/1000)
  }});
  const current=validated+700;
  const expires=deadline(response,rules,validated+500,current);
  assert.equal(expires,validated+1000);
  assert.equal(expires-current,300);
  assert.ok(deadline(response,rules,validated+1000,validated+1300)<=validated+1300);
  assert.equal(response.headers.get('x-market-validated-at'),String(validated));
 }
});

test('origin rejects malformed and future clocks without replacing their timestamps',async()=>{
 for(const clock of ['bad-clock','',String(Date.now()+500),'9999999999999999']){
  await withRuntime(async({request,stored,drain})=>{
   const response=await request('/api/market/v1/search?q=005930');await drain();
   assert.equal(stored.size,0);
   assert.equal(response.headers.get('x-market-validated-at'),clock);
  },{upstream:()=>new Response('{}',{headers:{
   'x-market-validated-at':clock,'x-accel-expires':'0','cache-control':'private, no-store'
  }})});
 }
});
