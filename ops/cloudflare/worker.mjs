import {legacyDestination} from './legacy-routes.mjs';

const PREFIX='/api/market/v1';
const CACHE_CONTRACT='public-read-v3-origin';
// Only pending public JSON reads are shared. Never retain a Response or stream
// across Worker invocations; each waiter constructs its own body from bytes.
const pendingReads=new Map(),MAX_PENDING=32,MAX_SHARED_BODY=64*1024;

async function shareableBody(response,rules,started) {
  const expires=deadline(response,rules,started,Date.now(),true);
  if(expires<=Date.now()||!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type')||'')||
      Number(response.headers.get('content-length'))>MAX_SHARED_BODY)return null;
  const reader=response.clone().body?.getReader();
  if(!reader)return null;
  const chunks=[];let size=0,timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;void reader.cancel().catch(()=>{});},1500);
  try{
    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      size+=value.byteLength;
      if(size>MAX_SHARED_BODY){void reader.cancel().catch(()=>{});return null;}
      chunks.push(value);
    }
    if(timedOut||expires<=Date.now())return null;
    const body=new Uint8Array(size);let offset=0;
    for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
    return {body,headers:[...response.headers],status:response.status,statusText:response.statusText,expires};
  }catch{return null;}
  finally{clearTimeout(timer);reader.releaseLock();}
}

async function sharedPublicFetch(identity,fetchOrigin,rules,started,ctx){
  const existing=pendingReads.get(identity);
  if(existing){
    const data=await existing.catch(()=>null);
    if(data&&data.expires>Date.now()){
      const response=new Response(data.body,{status:data.status,statusText:data.statusText,headers:data.headers});
      response.headers.delete('content-length');
      response.headers.set('X-Market-Coalesced','1');
      return response;
    }
    // Errors, oversized bodies or exhausted freshness are not shared/cache hits.
    return fetchOrigin();
  }
  if(pendingReads.size>=MAX_PENDING||identity.length>8192)return fetchOrigin();
  let original;
  const pending=(async()=>{
    original=await fetchOrigin();
    return shareableBody(original,rules,started);
  })();
  pendingReads.set(identity,pending);
  const cleanup=pending.catch(()=>{}).finally(()=>{
    if(pendingReads.get(identity)===pending)pendingReads.delete(identity);
  });
  // Keep the creator's I/O alive if its client disconnects; waiters receive only
  // data, and re-fetch independently if that creator's read cannot complete.
  ctx.waitUntil(cleanup);
  await pending;
  return original;
}
export function policy(url) {
  const path=url.pathname;
  if(path.startsWith('/_next/static/'))return {seconds:31536000,live:false};
  if(path===PREFIX+'/search-index'||path===PREFIX+'/analysis/metrics')return {seconds:300,live:false};
  if(path===PREFIX+'/search'||(/^\/api\/market\/v1\/securities\/[A-Z0-9]{6}$/.test(path)&&url.searchParams.get('view')==='summary'))return {seconds:1,live:true};
  if(/^\/api\/market\/v1\/securities\/[A-Z0-9]{6}\/financials(?:\/(per-share|timeline|ttm))?$/.test(path))return {seconds:300,live:false,requiresConfirmed:true};
  return null;
}
export function deadline(response, rules, started, current=Date.now(), originPartitioned=false) {
  if(!rules||response.status!==200||response.headers.has('set-cookie'))return 0;
  if(rules.requiresConfirmed&&response.headers.get('x-market-cache-state')!=='confirmed')return 0;
  const control=response.headers.get('cache-control')||'';
  // The one-second quote contract explicitly allows an edge copy despite the
  // browser's no-store. Private responses are never eligible for that exception.
  if(/(?:^|,)\s*private(?:\s|=|,|$)/i.test(control)||
     (!rules.live&&/(?:^|,)\s*(?:no-store|no-cache)(?:\s|,|$)/i.test(control)))return 0;
  const varies=(response.headers.get('vary')||'').split(',').map(x=>x.trim().toLowerCase()).filter(Boolean);
  if(varies.some(x=>x!=='accept-encoding'&&!(originPartitioned&&x==='origin')))return 0;
  if(rules.live){
    const source=Number(response.headers.get('x-market-validated-at'));
    if(!Number.isFinite(source)||source<=0||source>current+1000)return 0;
    return Math.min(started+1000,source+1000);
  }
  const age=Number(response.headers.get('age')||0);
  if(!Number.isFinite(age)||age<0)return 0;
  const upstreamDate=Date.parse(response.headers.get('date')||'');
  // A five-minute old upstream cache entry must not receive another five minutes.
  return Math.min(started+rules.seconds*1000,
    current+(rules.seconds-age)*1000,
    Number.isFinite(upstreamDate)?upstreamDate+rules.seconds*1000:Infinity);
}

function publicResponse(upstream,rules,request,cacheState,cacheAllowed=false){
  const response=new Response(upstream.body,upstream);
  response.headers.set('X-Market-Edge',cacheState);
  response.headers.set('X-Market-Edge-Contract',CACHE_CONTRACT);
  response.headers.delete('x-edge-expires');
  if(response.status>=400||rules?.live)response.headers.set('Cache-Control','no-store');
  else if(rules&&cacheAllowed&&!new URL(request.url).pathname.startsWith('/_next/static/')){
    // Revalidate at the nearby Worker. Do not restart a five-minute browser TTL
    // every time an almost-expired edge entry is read.
    response.headers.set('Cache-Control','public, max-age=0, must-revalidate');
  }
  if(cacheState==='HIT'){
    const condition=request.headers.get('if-none-match'),etag=response.headers.get('etag');
    const stripWeak=value=>value.trim().replace(/^W\//,'');
    if(condition&&etag&&(condition.trim()==='*'||condition.split(',').some(value=>stripWeak(value)===stripWeak(etag)))){
      response.headers.delete('content-length');
      response.headers.delete('content-encoding');
      return new Response(null,{status:304,headers:response.headers});
    }
  }
  return response;
}

export default {
  async fetch(request,env,ctx) {
    const url=new URL(request.url),origin=new URL(env.ORIGIN);
    const unavailable=()=>new Response('Not found',{status:404,headers:{'Cache-Control':'no-store'}});
    if(url.pathname.startsWith('/internal')||url.pathname.startsWith('/ops/')||
       /^\/api\/(?:auth|pf|market-auth|market-account)(?:\/|$)/.test(url.pathname)||
       /^\/api\/market\/v1\/(?:account|internal|ops)(?:\/|$)/.test(url.pathname))return unavailable();
    const target=legacyDestination(url);
    if(target){
      if(!['GET','HEAD'].includes(request.method))return new Response(null,{status:405,headers:{Allow:'GET, HEAD','Cache-Control':'no-store'}});
      return new Response(null,{status:302,headers:{Location:target,'Cache-Control':'no-store'}});
    }
    if(env.ASSETS&&!url.pathname.startsWith(PREFIX+'/')){
      if(url.pathname.startsWith('/api/'))return unavailable();
      // Asset router normally serves these without invoking the Worker. Missing
      // assets stay 404; they never fetch the old VPS HTML or an API SPA fallback.
      return env.ASSETS.fetch(request);
    }
    if(env.ASSETS){
      const postPaths=new Set([PREFIX+'/screener',PREFIX+'/screener/export',PREFIX+'/analysis/scenario']);
      const allowed=['GET','HEAD','OPTIONS'].includes(request.method)||(request.method==='POST'&&postPaths.has(url.pathname));
      if(!allowed)return new Response(null,{status:405,headers:{Allow:postPaths.has(url.pathname)?'GET, HEAD, OPTIONS, POST':'GET, HEAD, OPTIONS','Cache-Control':'no-store'}});
    }
    // The target is a fixed deployment setting, never a user-supplied proxy URL.
    origin.pathname=url.pathname;origin.search=url.search;
    const requestControl=request.headers.get('cache-control')||'';
    const rules=request.method==='GET'&&!['authorization','cookie','x-api-key','proxy-authorization'].some(name=>request.headers.has(name))&&
      !request.headers.has('range')&&request.headers.get('upgrade')?.toLowerCase()!=='websocket'&&
      !/(?:^|,)\s*(?:no-cache|no-store|max-age=0)(?:\s|,|$)/i.test(requestControl)?policy(url):null;
    const keyURL=new URL(url);keyURL.searchParams.sort();keyURL.searchParams.set('__release',env.RELEASE);
    keyURL.searchParams.set('__cache_contract',CACHE_CONTRACT);
    // CORS responses vary even when the incoming Origin is absent. Partition
    // both prime orders; never strip Vary or broaden Access-Control-Allow-Origin.
    // JSON distinguishes a missing Origin from the literal "null" header.
    keyURL.searchParams.set('__request_origin',JSON.stringify(request.headers.get('origin')));
    const keyHeaders=new Headers();
    if(request.headers.has('origin'))keyHeaders.set('Origin',request.headers.get('origin'));
    const key=new Request(keyURL,{method:'GET',headers:keyHeaders}),cache=caches.default;
    if(rules){
      try{
        const cached=await cache.match(key);
        if(cached&&Number(cached.headers.get('x-edge-expires'))>Date.now())return publicResponse(cached,rules,request,'HIT',true);
      }catch{/* A cache failure falls back to the healthy origin. */}
    }
    const started=Date.now();
    const headers=new Headers(request.headers);headers.delete('host');headers.delete('x-market-validated-at');
    let upstream;
    const fetchOrigin=()=>fetch(new Request(origin,request),{headers,redirect:'manual',cache:'no-store'});
    // Preserve the full query and representation/conditional headers. Origin and
    // release partitioning also apply to pending reads, not only Cache API keys.
    const identity=JSON.stringify([origin.href,env.RELEASE,...['origin','accept','accept-encoding',
      'if-none-match','if-modified-since','if-match','if-unmodified-since'].map(name=>request.headers.get(name))]);
    try {upstream=rules?.live
      ?await sharedPublicFetch(identity,fetchOrigin,rules,started,ctx):await fetchOrigin();}
    catch{return new Response('일시적으로 연결할 수 없어요.',{status:502,headers:{'Cache-Control':'no-store'}});}
    if(upstream.status===101)return upstream;
    const response=new Response(upstream.body,upstream);
    const expires=deadline(response,rules,started,Date.now(),true);
    if(expires>Date.now()){
      const cached=response.clone();cached.headers.set('Cache-Control',`public, max-age=${rules.seconds}`);
      cached.headers.set('x-edge-expires',String(expires));
      cached.headers.delete('x-market-coalesced');
      // Encoding is managed by the Workers fetch/Cache APIs; the cache retains
      // the response's encoding metadata rather than decoding its body in JS.
      // Both synchronous Cache API errors and asynchronous quota/storage errors
      // are optional acceleration failures, not application failures.
      ctx.waitUntil(Promise.resolve().then(()=>cache.put(key,cached)).catch(()=>{}));
    }
    return publicResponse(response,rules,request,rules?'MISS':'BYPASS',expires>Date.now());
  }
};
