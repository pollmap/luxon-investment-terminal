import type {Financial} from './types';
import {valuationPeriodChange,type ValuationPeriod} from './valuation-series';
export function financialPeriod(row:Financial):ValuationPeriod {
 return {period:row.period,period_start:row.period_start??row.input_period_start,period_end:row.period_end??row.input_period_end??'',frequency:row.frequency,basis:row.basis,value:row.value,available_at:row.available_at??row.input_published_at,
  reason:row.period_verified===true?row.period_reason??undefined:'actual_period_unverified'};
}
export function financialYearChange(row:Financial|undefined,rows:Financial[]){
 if(!row)return {percent:null,label:'—'};
 if(row.period_verified!==true)return {percent:null,label:'기간 미확인'};
 if(typeof row.unit!=='string'||!row.unit.trim())return {percent:null,label:'단위 미확인'};
 const comparable=rows.filter(candidate=>candidate.name===row.name&&candidate.unit===row.unit
   &&candidate.basis===row.basis&&candidate.frequency===row.frequency
   &&candidate.input_company_id===row.input_company_id&&candidate.period_verified===true);
 return valuationPeriodChange(financialPeriod(row),comparable.map(financialPeriod),{frequency:row.frequency,basis:row.basis});
}
