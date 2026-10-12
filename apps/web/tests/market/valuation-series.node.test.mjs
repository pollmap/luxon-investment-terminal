import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source=fs.readFileSync(new URL('../../components/market/valuation-series.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const context={exports:{}};vm.runInNewContext(code,context);
const {actualMultiple,selectedReference,valuationChartRows,latestValuationPeriods,periodIdentity,valuationPeriodChange}=context.exports;
const p=(extra={})=>({date:'2026-01-02',price:200,metric_value:10,multiple:20,comparison_eligible:true,reference_price:150,historical_reference:null,...extra});
const period=(period_start,period_end,value,extra={})=>({period:period_end.slice(0,4),period_start,period_end,value,frequency:'annual',basis:'CFS',available_at:'2026-03-01T00:00:00Z',...extra});
const ctx={frequency:'annual',basis:'CFS'};

test('actual multiple uses only explicit eligible, finite and positive server facts',()=>{
 assert.equal(actualMultiple(p()),20);assert.equal(selectedReference(p(),15),150);
 for(const value of [null,undefined,0,-1,NaN,Infinity]){assert.equal(actualMultiple(p({metric_value:value})),null);assert.equal(actualMultiple(p({multiple:value})),null);}
 assert.equal(actualMultiple(p({comparison_eligible:false})),null);
 assert.equal(selectedReference(p({comparison_eligible:false}),15),null);
 assert.equal(selectedReference(p({metric_value:Infinity}),15),null);
 assert.equal(selectedReference(p(),NaN),null);assert.equal(selectedReference(p(),101),null);
});
test('multiple chart keeps all dates and does not reconstruct rejected values from price',()=>{
 const points=[p(),p({date:'2026-01-03',comparison_eligible:false}),p({date:'2026-01-04',metric_value:-5}),p({date:'2026-01-05',multiple:null})];
 const before=JSON.stringify(points),rows=valuationChartRows(points,'multiple');
 assert.deepEqual(Array.from(rows,r=>r.time),points.map(p=>p.date));assert.equal(rows[0].close,20);
 for(const row of rows.slice(1)){assert.equal(row.valid,false);assert.equal(Number.isNaN(row.close),true);}
 assert.equal(valuationChartRows(points,'price')[1].close,200);assert.equal(JSON.stringify(points),before);
});
test('period identity preserves same label with different actual duration or frequency',()=>{
 const annual=period('2024-01-01','2024-12-31',10),short=period('2024-07-01','2024-12-31',11),ttm=period('2024-01-01','2024-12-31',12,{frequency:'ttm'});
 assert.notEqual(periodIdentity(annual),periodIdentity(short));assert.notEqual(periodIdentity(annual),periodIdentity(ttm));
 assert.equal(latestValuationPeriods([annual,short,ttm]).length,3);
});
test('restatements compare actual UTC publication times, not timezone string order',()=>{
 const first=period('2024-01-01','2024-12-31',10,{available_at:'2025-03-01T10:00:00+09:00'});
 const latest={...first,value:12,available_at:'2025-03-01T02:00:00Z'};
 assert.equal(latestValuationPeriods([latest,first])[0].value,12);
});
test('YoY matches both real endpoints and basis/frequency; a label does not establish comparison',()=>{
 const current=period('2024-01-01','2024-12-31',120),prior=period('2023-01-01','2023-12-31',100);
 assert.equal(valuationPeriodChange(current,[current,prior],ctx).label,'+20.0%');
 for(const changed of [{period_start:'2023-04-01'},{frequency:'ttm'},{basis:'OFS'},{reason:'period_unverified'}])assert.equal(valuationPeriodChange(current,[current,{...prior,...changed}],ctx).percent,null);
 assert.equal(valuationPeriodChange({...current,reason:'share_class_unverified'},[current,prior],ctx).percent,null);
 assert.equal(valuationPeriodChange({...current,period_start:undefined},[current,prior],ctx).label,'기간 미확인');
});
test('leap day clamps the prior year correctly and valid non-December years are comparable',()=>{
 const current=period('2023-03-01','2024-02-29',120),prior=period('2022-03-01','2023-02-28',100);
 assert.equal(valuationPeriodChange(current,[prior],ctx).label,'+20.0%');
 assert.equal(valuationPeriodChange({...current,period_end:'2024-02-30'},[prior],ctx).percent,null);
});
test('short/long fiscal years, missing amounts, zero denominators and negative transitions do not invent growth',()=>{
 const current=period('2024-01-01','2024-12-31',120),prior=period('2023-01-01','2023-12-31',100);
 for(const start of ['2024-04-01','2023-07-01'])assert.equal(valuationPeriodChange({...current,period_start:start},[prior],ctx).label,'기간 변경');
 for(const value of [null,undefined,NaN,Infinity])assert.equal(valuationPeriodChange({...current,value},[prior],ctx).percent,null);
 assert.equal(valuationPeriodChange(current,[{...prior,value:0}],ctx).label,'전년 0');
 assert.equal(valuationPeriodChange(current,[{...prior,value:-100}],ctx).label,'양수 전환');
 assert.equal(valuationPeriodChange({...current,value:-100},[prior],ctx).label,'음수 전환');
 assert.equal(valuationPeriodChange({...current,value:0},[prior],ctx).percent,-100);
});
