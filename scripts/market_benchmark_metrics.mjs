// Cached Server-Timing describes the generating request, not this cache hit.
export function applicationTiming(headers) {
  const cachedOrigin = ['HIT', 'STALE', 'UPDATING', 'REVALIDATED'].includes(headers['x-market-cache']);
  if (headers['x-market-edge'] === 'HIT' || cachedOrigin) return null;
  const match = /\bapp;dur=([\d.]+)/.exec(headers['server-timing'] || '');
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

export function percentile95(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  return sorted.length ? +sorted[Math.ceil(sorted.length * .95) - 1].toFixed(2) : null;
}

const timingNames=['app','revision_wait','revision_db','executor_wait','rows_lock','rows_inputs',
  'rows_build','selected_read','response_lock','response_build','response_encode','flight_wait'];
const counterNames=[...timingNames.filter(name=>name!=='app'),'rows_built','rows_reused',
  'response_hit','flight_join','revision_join'];
const rawHeaderNames=['server-timing','x-market-work','x-market-validated-at','date','age',
  'cache-control','x-market-cache','x-market-edge','x-market-edge-contract','content-encoding'];

// Diagnostics are observations only. Client and server wall clocks have not
// been synchronized; a positive budget does not prove an edge cache entry exists.
export function responseDiagnostics(headers, headersAtMs, endAtMs) {
  const rawHeaders=Object.fromEntries(rawHeaderNames.map(name=>[
    name,typeof headers[name]==='string'?headers[name]:null,
  ]));
  const current=applicationTiming(headers)!==null;
  const stages=current?{}:null,counts=current?{}:null;
  if(current){
    for(const part of (rawHeaders['server-timing']||'').split(',')){
      const match=/^\s*([a-z_]+);dur=(\d+(?:\.\d+)?)\s*$/.exec(part);
      if(match&&timingNames.includes(match[1])&&Number.isFinite(Number(match[2])))
        stages[match[1]]=Number(match[2]);
    }
    for(const part of (rawHeaders['x-market-work']||'').split(',')){
      const match=/^\s*([a-z_]+)=(\d+)\s*$/.exec(part);
      if(match&&counterNames.includes(match[1])&&Number.isSafeInteger(Number(match[2])))
        counts[match[1]]=Number(match[2]);
    }
  }
  const raw=rawHeaders['x-market-validated-at'];
  const validated=raw&&/^[0-9]{1,16}$/.test(raw)?Number(raw):NaN;
  const budget=at=>{
    if(!Number.isSafeInteger(validated)||validated<=0||!Number.isFinite(at))
      return {state:'invalid_or_missing',age_ms:null,remaining_ms:null};
    const age=at-validated;
    if(age<0)return {state:'future_or_clock_skew',age_ms:age,remaining_ms:null};
    return {state:age>=1000?'exhausted':'within_budget_unverified',
      age_ms:age,remaining_ms:Math.max(0,1000-age)};
  };
  return {raw_headers:rawHeaders,timing_is_current:current,stages_ms:stages,work_counts:counts,
    freshness:{clock_basis:'client_wall_clock_unverified',headers_at_ms:headersAtMs,end_at_ms:endAtMs,
      at_headers:budget(headersAtMs),at_end:budget(endAtMs)}};
}
