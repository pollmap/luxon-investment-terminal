import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  let now=1000;
  const calls=[];
  const request=(path,{signal})=>new Promise((resolve,reject)=>{
    calls.push({path,signal,resolve,reject});
    signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  });
  const source=fs.readFileSync(new URL('../../components/market/first-chart-request.ts',import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const context={exports:{},require(name){assert.equal(name,'./types');return {request};},Date:{now:()=>now},AbortController,DOMException,queueMicrotask};
  vm.runInNewContext(code,context);
  return {create:context.exports.createFirstChartRequest,calls,advance(ms){now+=ms;}};
}
const line='/securities/005930/chart?period=day';

test('one initial response is shared, including a discarded effect, without changing its source date',async()=>{
  const h=harness(),loader=h.create(line),first=new AbortController(),second=new AbortController();
  loader.preload();loader.preload();
  const abandoned=loader.read(line,first.signal).catch(e=>e.name);first.abort();
  const value=loader.read(line,second.signal);
  const raw={session_date:'2026-10-07',data_as_of:'2026-10-07T15:30:00+09:00',points:[{value:99}]};
  h.calls[0].resolve(raw);
  assert.equal(await abandoned,'AbortError');assert.equal(await value,raw);
  assert.equal(await loader.read(line,second.signal),raw);
  loader.preload();assert.equal(h.calls.length,1);
});

for(const [path,age] of [[line,60000],['/securities/005930/bars?interval=5min',30000],['/securities/005930/price-window?range=252&interval=day',60000]]) {
  test(`expired initial response is replaced at ${age}ms: ${path}`,async()=>{
    const h=harness(),loader=h.create(path);loader.preload();h.calls[0].resolve({marker:'old'});
    await Promise.resolve();h.advance(age);
    const fresh=loader.read(path,new AbortController().signal);
    assert.equal(h.calls.length,2);assert.equal(h.calls[0].signal.aborted,true);
    const raw={marker:'new',data_as_of:'unchanged-source-time'};h.calls[1].resolve(raw);
    assert.equal(await fresh,raw);
  });
}

test('selection change and real unmount abort only the old pending request',async()=>{
  const h=harness(),old=h.create(line),next=h.create('/securities/000660/chart?period=week');
  old.preload();old.cancel();next.preload();await Promise.resolve();
  assert.equal(h.calls[0].signal.aborted,true);assert.equal(h.calls[1].signal.aborted,false);
  next.cancel();await Promise.resolve();assert.equal(h.calls[1].signal.aborted,true);
});

test('synchronous effect cleanup and replay preserve the same pending request',async()=>{
  const h=harness(),loader=h.create(line);loader.preload();loader.cancel();
  const value=loader.read(line,new AbortController().signal);await Promise.resolve();
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].signal.aborted,false);
  h.calls[0].resolve({value:1});assert.equal((await value).value,1);
});

test('prefetch rejection is handled until its own consumer reads it',async()=>{
  const h=harness(),loader=h.create(line);loader.preload();h.calls[0].reject(new Error('fixture chart error'));
  await Promise.resolve();await Promise.resolve();
  await assert.rejects(loader.read(line,new AbortController().signal),/fixture chart error/);
  assert.equal(h.calls.length,1);
});

test('explicit chart retry resets only its initial response',async()=>{
  const h=harness(),loader=h.create(line);loader.preload();h.calls[0].reject(new Error('first failure'));
  await assert.rejects(loader.read(line,new AbortController().signal),/first failure/);
  loader.reset();const retry=loader.read(line,new AbortController().signal);
  h.calls[1].resolve({recovered:true});assert.equal((await retry).recovered,true);assert.equal(h.calls.length,2);
});
