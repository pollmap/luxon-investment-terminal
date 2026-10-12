import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';
const require=createRequire(import.meta.url);
const {renderToStaticMarkup}=require('react-dom/server');
const source=fs.readFileSync(new URL('../../components/market/financial-questions.tsx',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
const context={exports:{},require:name=>name==='./types'?{format:value=>value==null?'—':String(value),request:()=>{throw Error('No network');}}:require(name)};
vm.runInNewContext(code,context);
const row=(metric,value,status='ready',extra={})=>({metric,name:metric,value,status,unit:'원',...extra});
const input=(rows,comparisons=[])=>({code:'000001',basis:'CFS',frequency:'ttm',period_start:'2025-07-01',period_end:'2026-06-30',rows,comparisons});
const render=data=>renderToStaticMarkup(context.exports.FinancialQuestionsView({data}));
test('confirmed zero and negative cash flow remain numbers; unverified nonzero is not exposed',()=>{
 const html=render(input([row('net_income',0),row('operating_cash',-100000000),row('capex',999000000,'unavailable',{reason_label:'원문 확인 필요'})]));
 assert.match(html,/>0</);assert.match(html,/>-1</);assert.doesNotMatch(html,/999/);assert.match(html,/원문 확인 필요/);
 assert.match(html,/회사 전체 금액/);assert.match(html,/2025-07-01 ~ 2026-06-30/);
});
test('missing values are not rendered as 0 or ratios reconstructed on the client',()=>{
 const html=render(input([row('net_income',100000000),row('operating_cash',null,'unavailable')],[row('cash_conversion',null,'unavailable',{reason_label:'같은 기간 입력 없음'})]));
 assert.match(html,/같은 기간 입력 없음/);assert.doesNotMatch(html,/>0%/);
});
test('without confirmed amounts the view is one short state, not empty numeric cards',()=>{
 const html=render(input([row('net_income',null,'unavailable')]));
 assert.match(html,/원문 근거가 아직 부족/);assert.doesNotMatch(html,/<table|0%/);
});
test('only validated filing ids become external source links and labels are escaped',()=>{
 const html=render(input([row('net_income',100000000,'ready',{name:'<img src=x>',source_trace:{inputs:[{filing_id:'20250814001234'},{filing_id:'javascript:bad'},{filing_id:'20250814001234'}]}})]));
 assert.match(html,/&lt;img src=x&gt;/);assert.equal((html.match(/href=/g)||[]).length,1);
 assert.match(html,/https:\/\/dart.fss.or.kr\/dsaf001\/main.do\?rcpNo=20250814001234/);assert.doesNotMatch(html,/javascript:bad/);
});
