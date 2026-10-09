import test from 'node:test';
import assert from 'node:assert/strict';
import {applicationTiming, percentile95, responseDiagnostics} from './market_benchmark_metrics.mjs';

test('origin and edge hits do not count historical app processing as current', () => {
  for (const state of ['HIT', 'STALE', 'UPDATING', 'REVALIDATED']) {
    assert.equal(applicationTiming({'x-market-cache': state, 'server-timing': 'app;dur=680.12'}), null);
  }
  assert.equal(applicationTiming({'x-market-edge': 'HIT', 'x-market-cache': 'MISS', 'server-timing': 'app;dur=680.12'}), null);
  assert.equal(applicationTiming({'x-market-cache': 'EXPIRED', 'server-timing': 'app;dur=0.00'}), 0);
  assert.equal(applicationTiming({'x-market-cache': 'MISS', 'server-timing': 'app;dur=23.42'}), 23.42);
  assert.equal(applicationTiming({}), null);
});

test('missing timings are excluded rather than converted to zero', () => {
  assert.equal(percentile95([null, undefined, NaN, Infinity]), null);
  assert.equal(percentile95([null, 0, 23, 40]), 40);
  assert.equal(percentile95(Array.from({length: 100}, (_, i) => i + 1)), 95);
});

test('collect fixed work stages and raw cache clocks without copying sensitive headers', () => {
  const headers={'server-timing':'app;dur=500.00, revision_db;dur=4.25, executor_wait;dur=80.00, private_key;dur=7',
    'x-market-work':'revision_db=1, rows_built=3, private_key=5',
    'x-market-validated-at':'1791477600100','age':'1','date':'Fri, 09 Oct 2026 18:46:26 GMT',
    'x-market-cache':'EXPIRED','x-market-edge':'MISS','cache-control':'no-store',
    'authorization':'secret','set-cookie':'private-cookie','x-request-url':'/005930'};
  const result=responseDiagnostics(headers,1791477600300,1791477600350);
  assert.equal(result.timing_is_current,true);
  assert.deepEqual(result.stages_ms,{app:500,revision_db:4.25,executor_wait:80});
  assert.deepEqual(result.work_counts,{revision_db:1,rows_built:3});
  assert.equal(result.raw_headers.age,'1');
  assert.equal(result.raw_headers.date,headers.date);
  assert.equal(result.raw_headers['x-market-validated-at'],headers['x-market-validated-at']);
  assert.equal(result.freshness.clock_basis,'client_wall_clock_unverified');
  assert.deepEqual(result.freshness.at_headers,{state:'within_budget_unverified',age_ms:200,remaining_ms:800});
  assert.deepEqual(result.freshness.at_end,{state:'within_budget_unverified',age_ms:250,remaining_ms:750});
  assert(!JSON.stringify(result).includes('secret'));
  assert(!JSON.stringify(result).includes('005930'));
  assert(!('authorization' in result.raw_headers));
});

test('cached upstream measurements remain raw evidence but are not current work', () => {
  for(const cache of [
    {'x-market-cache':'HIT'}, {'x-market-cache':'STALE'}, {'x-market-cache':'UPDATING'},
    {'x-market-cache':'REVALIDATED'}, {'x-market-edge':'HIT','x-market-cache':'EXPIRED'},
  ]){
    const result=responseDiagnostics({...cache,'server-timing':'app;dur=500.00, revision_db;dur=30.00',
      'x-market-work':'rows_built=2763','x-market-validated-at':'10000','age':'2'},12000,12001);
    assert.equal(result.timing_is_current,false);
    assert.equal(result.stages_ms,null);
    assert.equal(result.work_counts,null);
    assert.equal(result.raw_headers['x-market-work'],'rows_built=2763');
    assert.equal(result.freshness.at_headers.state,'exhausted');
    assert.equal(result.freshness.at_headers.remaining_ms,0);
  }
});

test('one-second budget boundaries do not restart the validation clock', () => {
  for(const age of [0,1,999,1000,5000]){
    const result=responseDiagnostics({'x-market-validated-at':'10000','age':'0'},10000+age,10000+age);
    assert.deepEqual(result.freshness.at_headers,{
      state:age>=1000?'exhausted':'within_budget_unverified',age_ms:age,remaining_ms:Math.max(0,1000-age),
    });
    assert.equal(result.raw_headers['x-market-validated-at'],'10000');
  }
});

test('missing malformed and future clocks never become fresh observations', () => {
  for(const clock of [undefined,'invalid','0','-1','1e4','9007199254740992','10000.5']){
    const result=responseDiagnostics({'x-market-validated-at':clock},10000,10010);
    assert.deepEqual(result.freshness.at_headers,{state:'invalid_or_missing',age_ms:null,remaining_ms:null});
  }
  const future=responseDiagnostics({'x-market-validated-at':'10001'},10000,10000);
  assert.deepEqual(future.freshness.at_headers,{state:'future_or_clock_skew',age_ms:-1,remaining_ms:null});
  const noHeaders=responseDiagnostics({'x-market-validated-at':'10000'},null,10010);
  assert.equal(noHeaders.freshness.at_headers.state,'invalid_or_missing');
  assert.equal(noHeaders.timing_is_current,false);
  assert.equal(noHeaders.stages_ms,null);
});

test('stage parsing omits missing non-finite negative and unrecognized measurements', () => {
  const result=responseDiagnostics({
    'server-timing':'app;dur=1.25, revision_db;dur=-1, rows_build;dur=Infinity, rows_lock;dur=0.00',
    'x-market-work':'rows_built=-5, response_hit=1, rows_reused=Infinity, revision_db=9007199254740992',
  },10000,10001);
  assert.deepEqual(result.stages_ms,{app:1.25,rows_lock:0});
  assert.deepEqual(result.work_counts,{response_hit:1});
});
