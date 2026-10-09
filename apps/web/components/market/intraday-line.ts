export type LinePoint = {time:string;value:number};
export type LineInterval = "1min" | "5min";

export function resolveLineInterval(shared: unknown, saved: unknown, phone: boolean): LineInterval {
  if(shared === "1min" || shared === "5min") return shared;
  if(saved === "1min" || saved === "5min") return saved;
  return phone ? "5min" : "1min";
}

function epoch(time:string):number {
  if(/^\d{12}(\d{2})?$/.test(time)) return Date.parse(`${time.slice(0,4)}-${time.slice(4,6)}-${time.slice(6,8)}T${time.slice(8,10)}:${time.slice(10,12)}:${time.slice(12,14)||"00"}+09:00`);
  return time.includes("T") ? Date.parse(time) : NaN;
}

/** Display-only sampling: retain the actual last observation of each five-minute
 * bucket. No averages, synthetic timestamps, gap filling or OHLC construction. */
export function intradayLinePoints<T extends LinePoint>(points:T[], interval:LineInterval):T[] {
  if(interval === "1min") return points;
  const buckets=new Map<number,{point:T;time:number}>();
  for(const point of points){
    const time=epoch(point.time);
    // Daily/unsupported series is not silently reinterpreted as minute data.
    if(!Number.isFinite(time)) return points;
    const key=Math.floor(time/300000), previous=buckets.get(key);
    if(!previous || time>=previous.time) buckets.set(key,{point,time});
  }
  return [...buckets.entries()].sort(([a],[b])=>a-b).map(([,entry])=>entry.point);
}
