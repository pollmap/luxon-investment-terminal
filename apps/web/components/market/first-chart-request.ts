import {request} from './types';
import type {DetailSelection} from './detail-preferences';

export function intradayChartPath(code:string, period:string, kind:string) {
  return kind==='candle'||kind==='bar'
    ? `/securities/${code}/bars?interval=5min&venue=%ED%86%B5%ED%95%A9&adjustment=raw`
    : `/securities/${code}/chart?period=${period}`;
}

export function historyChartPath(code:string, range:number, bar:string) {
  return `/securities/${code}/price-window?range=${range}&interval=${bar}`;
}

export function detailChartPath(code:string, selection:DetailSelection) {
  return selection.mode==='intraday'
    ? intradayChartPath(code,selection.period,selection.kind)
    : historyChartPath(code,selection.range,selection.bar);
}

type Result={ok:true;value:unknown}|{ok:false;error:unknown};
type Pending={abort:AbortController;result:Promise<Result>;startedAt:number};

// One detail instance, one initial response (also reused by React's effect replay).
// Polls use request() normally, without a cross-page cache or a new freshness policy.
export function createFirstChartRequest(path:string) {
  let pending:Pending|undefined;
  let teardown=0,delivered=false;
  const maxAge=path.includes('/bars?')?30000:60000;
  const reset=()=>{pending?.abort.abort();pending=undefined;delivered=false;};
  const start=()=>{
    teardown++;
    if(pending&&Date.now()-pending.startedAt>=maxAge){pending.abort.abort();pending=undefined;}
    if(!pending) {
      const abort=new AbortController();
      const startedAt=Date.now();
      const result=request<unknown>(path,{signal:abort.signal}).then<Result,Result>(
        value=>({ok:true,value}),error=>({ok:false,error}));
      pending={abort,result,startedAt};
    }
    return pending;
  };
  return {
    preload(){if(!delivered)start();},
    async read<T>(requestedPath:string, signal:AbortSignal):Promise<T> {
      if(signal.aborted)throw new DOMException('Aborted','AbortError');
      if(requestedPath!==path)return request<T>(requestedPath,{signal});
      const first=start();
      const result=await first.result;
      // The detail owner cancels the network on selection change/unmount. A
      // discarded React effect must not consume the next effect's handoff.
      if(signal.aborted)throw new DOMException('Aborted','AbortError');
      delivered=true;
      if(!result.ok)throw result.error;
      return result.value as T;
    },
    cancel(){
      // React replays effects synchronously in development. Release after that
      // turn, but abort a real unmount/selection change before another task runs.
      const generation=++teardown;
      queueMicrotask(()=>{if(generation===teardown)reset();});
    },
    reset,
  };
}

export type FirstChartRequest=ReturnType<typeof createFirstChartRequest>;
