import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Fixture-only hook lifecycle: exercise the real timeout/abort/visibility logic.
function harness(){
  const cells=[],effects=[],pending=[],timers=new Set(),listeners=new Set(),calls=[];
  let slot=0;
  const document={hidden:false,addEventListener:(_,fn)=>listeners.add(fn),removeEventListener:(_,fn)=>listeners.delete(fn)};
  const request=(path,options)=>new Promise((resolve,reject)=>calls.push({path,options,resolve,reject}));
  const react={
    useState(value){const i=slot++;if(!(i in cells))cells[i]=value;return [cells[i],next=>{cells[i]=typeof next==='function'?next(cells[i]):next;}];},
    useEffect(fn,deps){const i=slot++;if(!effects[i]||deps.some((v,n)=>v!==effects[i].deps[n]))pending.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
  };
  const source=fs.readFileSync(new URL('../../components/market/use-visible-query.ts',import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const context={exports:{},require:name=>name==='react'?react:{request},document,AbortController,AbortSignal,
    setInterval(fn){timers.add(fn);return fn;},clearInterval:fn=>timers.delete(fn)};
  vm.runInNewContext(code,context);
  return {calls,timers,listeners,document,render(path){slot=0;const state=context.exports.useVisibleQuery(path,60000);for(const fn of pending.splice(0))fn();return state;},tick(){for(const fn of timers)fn();},visible(){for(const fn of listeners)fn();},unmount(){for(const effect of effects)effect?.cleanup?.();}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('hidden pages and closed panels issue no requests; visibility resumes once',async()=>{
  const h=harness();h.document.hidden=true;h.render('/indices/KOSPI/chart?period=day');h.tick();assert.equal(h.calls.length,0);
  h.document.hidden=false;h.visible();h.visible();h.tick();assert.equal(h.calls.length,1);
  h.calls[0].resolve({points:[1,2]});await settle();assert.deepEqual(h.render('/indices/KOSPI/chart?period=day').data,{points:[1,2]});
  h.render(null);assert.equal(h.timers.size,0);assert.equal(h.listeners.size,0);assert.equal(h.calls[0].options.signal.aborted,true);
  h.tick();h.visible();assert.equal(h.calls.length,1);
});
test('failed refresh retains last valid data; a different path never borrows it',async()=>{
  const h=harness();h.render('/a');h.calls[0].resolve({value:123});await settle();h.render('/a');
  h.tick();h.calls[1].reject(new Error('fixture outage'));await settle();
  const previous=h.render('/a');assert.equal(previous.data.value,123);assert.equal(previous.error,true);
  assert.equal(h.render('/b').data,null);h.calls[2].resolve({value:456});await settle();assert.equal(h.render('/b').data.value,456);
  h.unmount();
});
test('an aborted obsolete request cannot overwrite the next query or error',async()=>{
  const h=harness();h.render('/a');h.render('/b');assert.equal(h.calls[0].options.signal.aborted,true);
  h.calls[1].resolve({value:2});await settle();h.calls[0].resolve({value:1});await settle();
  assert.equal(h.render('/b').data.value,2);assert.equal(h.render('/b').error,false);h.unmount();
});
