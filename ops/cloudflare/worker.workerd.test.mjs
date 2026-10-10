// Optional native runtime check; all origin traffic is an in-process fixture.
// MARKET_WORKER_TEST_MODULES points to a local node_modules containing miniflare
// (v5) and esbuild. It installs nothing and makes no provider/origin requests.
import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const modules=process.env.MARKET_WORKER_TEST_MODULES;
test('workerd: independent invocations share bytes, preserve gzip and expire safely',
  {skip:!modules,timeout:20000},async t=>{
  const {Miniflare,convertV4MiniflareOptions}=await import(pathToFileURL(join(modules,'miniflare/dist/src/index.js')));
  const {build}=await import(pathToFileURL(join(modules,'esbuild/lib/main.js')));
  const {outputFiles}=await build({
    entryPoints:[fileURLToPath(new URL('./worker.mjs',import.meta.url))],
    bundle:true,write:false,format:'esm',platform:'browser',target:'es2022',
  });
  const calls=new Map(), clocks=new Map();
  const options=convertV4MiniflareOptions({
    modules:true,compatibilityDate:'2026-10-03',cf:false,script:outputFiles[0].text,
    bindings:{ORIGIN:'https://fixture.invalid',RELEASE:'native-fixture'},
    outboundService:async request=>{
      assert.equal(new URL(request.url).hostname,'fixture.invalid');
      const scenario=new URL(request.url).searchParams.get('case');
      calls.set(scenario,(calls.get(scenario)||0)+1);
      await new Promise(done=>setTimeout(done,120));
      const clock=Date.now();clocks.set(scenario,clock);
      const body=JSON.stringify({rows:[{code:'005930',name:'삼성전자',price:1234}],scenario});
      const headers={'content-type':'application/json','x-market-validated-at':String(clock),
        'cache-control':'no-store','server-timing':'app;dur=25','vary':'Accept-Encoding'};
      if(scenario==='gzip'){
        // Miniflare's Node outbound handler compresses according to this header
        // when writing its loopback HTTP response. Supply logical bytes once.
        headers['content-encoding']='gzip';
      }
      if(scenario==='expired')headers['x-market-validated-at']=String(clock-1100);
      return new Response(body,{headers});
    },
  });
  options.telemetry={enabled:false};
  const runtime=new Miniflare(options);
  try{
    // Start the runtime before simultaneous requests, excluding startup latency.
    await runtime.ready;
    for(const scenario of ['plain','gzip'])await t.test(scenario,async()=>{
      const responses=await Promise.all(Array.from({length:4},()=>runtime.dispatchFetch(
        `https://worker.invalid/api/market/v1/search?case=${scenario}`,
        {headers:{'accept-encoding':'gzip'}})));
      assert.equal(calls.get(scenario),1);
      assert.equal(responses.filter(r=>r.headers.get('x-market-coalesced')==='1').length,3);
      // Cancelling one caller's response must not consume the other bodies.
      await responses[0].body.cancel();
      for(const response of responses.slice(1)){
        assert.equal(response.status,200);
        assert.equal(response.headers.get('x-market-validated-at'),String(clocks.get(scenario)));
        assert.equal(response.headers.get('cache-control'),'no-store');
        if(scenario==='gzip')assert.equal(response.headers.get('mf-content-encoding'),'gzip');
        const clone=response.clone();
        assert.deepEqual(await response.json(),await clone.json());
      }
    });
    await t.test('expired data cannot be handed from one request to its waiters',async()=>{
      const responses=await Promise.all(Array.from({length:3},()=>runtime.dispatchFetch(
        'https://worker.invalid/api/market/v1/search?case=expired')));
      assert.equal(calls.get('expired'),3);
      for(const response of responses){
        assert.equal(response.headers.get('x-market-coalesced'),null);
        assert.ok(Date.now()-Number(response.headers.get('x-market-validated-at'))>=1100);
        assert.equal((await response.json()).scenario,'expired');
      }
    });
  }finally{await runtime.dispose();}
});
