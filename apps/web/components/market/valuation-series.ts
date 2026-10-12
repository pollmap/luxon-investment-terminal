// These adapters only display server-validated facts. They do not establish
// financial eligibility or reconstruct a rejected multiple from price / EPS.
export type ValuationPoint={date:string;price:number;metric_value:number|null;multiple:number|null;comparison_eligible:boolean;reference_price:number|null;historical_reference:number|null};
export type ValuationPeriod={period:string;period_start?:string;period_end:string;frequency?:string;basis?:string;value:number|null;available_at?:string;reason?:string};
export function actualMultiple(point:Pick<ValuationPoint,"comparison_eligible"|"metric_value"|"multiple">):number|null {
  return point.comparison_eligible===true&&typeof point.metric_value==='number'&&Number.isFinite(point.metric_value)&&point.metric_value>0
    &&typeof point.multiple==='number'&&Number.isFinite(point.multiple)&&point.multiple>0?point.multiple:null;
}
export function selectedReference(point:Pick<ValuationPoint,"comparison_eligible"|"metric_value">,factor:number):number|null {
  if(point.comparison_eligible!==true||typeof point.metric_value!=='number'||!Number.isFinite(point.metric_value)||point.metric_value<=0||!Number.isFinite(factor)||factor<=0||factor>100)return null;
  const result=point.metric_value*factor;return Number.isFinite(result)?result:null;
}
export function valuationChartRows(points:ValuationPoint[],chart:'price'|'multiple'){
  return points.map(point=>{const value=chart==='multiple'?actualMultiple(point):point.price;return {time:point.date,close:typeof value==='number'&&Number.isFinite(value)?value:NaN,valid:typeof value==='number'&&Number.isFinite(value)};});
}
export function periodIdentity(period:ValuationPeriod):string {
  return JSON.stringify([period.period_start??'',period.period_end,period.frequency??'',period.period]);
}
const publication=(value:string|undefined)=>{const at=Date.parse(value??'');return Number.isFinite(at)?at:-Infinity;};
export function latestValuationPeriods<T extends ValuationPeriod>(periods:T[]):T[]{
  const latest=new Map<string,T>();
  for(const period of periods){const key=periodIdentity(period),old=latest.get(key);if(!old||publication(period.available_at)>=publication(old.available_at))latest.set(key,period);}
  return [...latest.values()].sort((a,b)=>b.period_end.localeCompare(a.period_end)||(b.period_start??'').localeCompare(a.period_start??''));
}
function day(value:string|undefined):Date|null {
  if(!value||!/^\d{4}-\d{2}-\d{2}$/.test(value))return null;
  const parsed=new Date(`${value}T00:00:00Z`);return Number.isFinite(parsed.valueOf())&&parsed.toISOString().slice(0,10)===value?parsed:null;
}
function previousYear(value:string):string|null {
  const parsed=day(value);if(!parsed)return null;
  const year=parsed.getUTCFullYear()-1,month=parsed.getUTCMonth(),last=new Date(Date.UTC(year,month+1,0)).getUTCDate();
  return new Date(Date.UTC(year,month,Math.min(parsed.getUTCDate(),last))).toISOString().slice(0,10);
}
export function valuationPeriodChange(period:ValuationPeriod,periods:ValuationPeriod[],context:{frequency:string;basis:string}):{percent:number|null;label:string}{
  const unavailable=(label='비교 불가')=>({percent:null,label});
  if(period.reason)return unavailable();
  const start=day(period.period_start),end=day(period.period_end),frequency=period.frequency??context.frequency,basis=period.basis??context.basis;
  if(!start||!end||end<start)return unavailable('기간 미확인');
  const duration=(end.valueOf()-start.valueOf())/86400000+1;
  if((frequency==='annual'||frequency==='ttm')&&(duration<330||duration>400))return unavailable('기간 변경');
  const priorStart=previousYear(period.period_start!),priorEnd=previousYear(period.period_end);
  const prior=periods.find(candidate=>candidate.period_start===priorStart&&candidate.period_end===priorEnd
    &&(candidate.frequency??context.frequency)===frequency&&(candidate.basis??context.basis)===basis&&!candidate.reason);
  if(!prior)return unavailable('전년 자료 없음');
  const current=period.value,previous=prior.value;
  if(typeof current!=='number'||typeof previous!=='number'||!Number.isFinite(current)||!Number.isFinite(previous))return unavailable();
  if(previous<0)return unavailable(current>0?'양수 전환':current<0?'음수 지속':'0 전환');
  if(current<0)return unavailable('음수 전환');
  if(previous===0)return unavailable('전년 0');
  const percent=(current/previous-1)*100;
  return Number.isFinite(percent)?{percent,label:`${percent>0?'+':''}${percent.toFixed(1)}%`}:unavailable();
}
