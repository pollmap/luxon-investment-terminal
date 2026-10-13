import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
const {renderToStaticMarkup}=require('react-dom/server');
function load(file,dependencies={},globals={}){
  const source=fs.readFileSync(new URL(`../../components/market/${file}`,import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const context={exports:{},require:name=>dependencies[name]??require(name),...globals};
  vm.runInNewContext(code,context);return context.exports;
}
const periods=load('analysis-periods.ts');
const native=value=>JSON.parse(JSON.stringify(value));
const f=(period,name='매출액',value=100,extra={})=>({period,name,value,frequency:'annual',basis:'CFS',estimate:false,unit:'억원',...(/^\d{4}$/.test(period)?{period_start:`${period}-01-01`,period_end:`${period}-12-31`,period_verified:true}:{}),source_trace:{source:'fixture_non_production',period,unit:'억원'},...extra});
function elements(tree,predicate){
  if(Array.isArray(tree))return tree.flatMap(n=>elements(n,predicate));
  if(!tree||typeof tree!=='object')return [];
  return [...(predicate(tree)?[tree]:[]),...elements(tree.props?.children,predicate)];
}
function text(tree){if(Array.isArray(tree))return tree.map(text).join('');if(tree==null||typeof tree==='boolean')return '';return typeof tree==='object'?text(tree.props.children):String(tree);}
const button=(h,label)=>elements(h.tree,n=>n.type==='button'&&text(n)===label)[0];
function hooks(overrides={}){
  const cells=[];let slot=0;
  return {reset(){slot=0;},react:{
    useState(initial){const i=slot++;if(!(i in cells))cells[i]=i in overrides?overrides[i]:initial;return [cells[i],value=>{cells[i]=typeof value==='function'?value(cells[i]):value;}];},
    useMemo:fn=>fn(),useEffect(){},useRef:value=>({current:value}),useId:()=> 'fixture-analysis',
  }};
}
const types={format:value=>value==null?'—':String(value),tone:()=>'',request:()=>{throw new Error('No network in this fixture');}};
function earnings(financials){
  const h=hooks();const Component=load('earnings-overview.tsx',{react:h.react,'./types':types,'./analysis-periods':periods,'./financial-change':load('financial-change.ts',{'./valuation-series':load('valuation-series.ts')})}).default;
  return {tree:null,render(){h.reset();this.tree=Component({code:'000001',financials});return renderToStaticMarkup(this.tree);}};
}
const headingYears=h=>elements(h.tree,n=>n.type==='thead')[0].props.children.props.children.slice(1)[0].map(text);

test('recent annual periods are contiguous, not the last available rows; all preserves history',()=>{
  const observed=['2021','2023','2024'];
  assert.deepEqual(native(periods.earningsPeriodWindow(observed,'annual',3)),{periods:['2022','2023','2024'],anchor:'2024',annualYears:true,missing:['2022']});
  assert.deepEqual(Array.from(periods.earningsPeriodWindow(observed,'annual',5).periods),['2020','2021','2022','2023','2024']);
  assert.equal(periods.earningsPeriodWindow(observed,'annual',10).periods[0],'2015');
  assert.deepEqual(Array.from(periods.earningsPeriodWindow(observed,'annual',0).periods),observed);
});
test('source fiscal labels set the anchor without assuming December close or current year',()=>{
  const observed=['2021/03','2023/03','2024/03'].map(periods.annualPeriodLabel);
  const result=periods.earningsPeriodWindow(observed,'annual',3);
  assert.deepEqual(Array.from(result.periods),['2022','2023','2024']);
  assert.equal(result.anchor,'2024');assert.equal(periods.annualPeriodLabel('FY2024'),'FY2024');
  assert.equal(periods.annualPeriodLabel('2024/13'),'2024/13');
});
test('unknown fiscal calendar labels and quarterly observations never invent periods',()=>{
  const unknown=periods.earningsPeriodWindow(['FY2021','FY2023','FY2024'],'annual',3);
  assert.equal(unknown.annualYears,false);assert.equal(unknown.anchor,null);
  assert.deepEqual(Array.from(unknown.periods),['FY2021','FY2023','FY2024']);
  assert.deepEqual(Array.from(periods.earningsPeriodWindow(['2024/03','2024/09'],'quarter',3).periods),['2024/03','2024/09']);
  assert.deepEqual(Array.from(periods.earningsPeriodWindow([],'annual',3).periods),[]);
});
test('missing and invalid values remain null while actual zero and losses remain numbers',()=>{
  for(const value of [null,undefined,NaN,Infinity])assert.equal(periods.observedFinancialValue(value),null);
  assert.equal(periods.observedFinancialValue(0),0);assert.equal(periods.observedFinancialValue(-100),-100);
});
test('rendered recent three years shows missing 2022 rather than substituting 2021',()=>{
  const input=[f('2021'),f('2023'),f('2024')],before=JSON.stringify(input),h=earnings(input),html=h.render();
  assert.deepEqual(Array.from(headingYears(h)),['2022','2023','2024']);
  assert.match(html,/2024 회계연도까지/);assert.match(html,/2022 매출액 자료 없음/);assert.match(html,/자료 없음/);
  assert.doesNotMatch(html,/>2021</);assert.equal(JSON.stringify(input),before);
  const bar=elements(h.tree,n=>n.props?.className==='mk-earnings-bars')[0].props.children[0];
  assert.equal(elements(bar,n=>n.type==='i').length,0);
  button(h,'최근 5년').props.onClick();h.render();assert.deepEqual(Array.from(headingYears(h)),['2020','2021','2022','2023','2024']);
  button(h,'최근 10년').props.onClick();h.render();assert.equal(headingYears(h).length,10);
  button(h,'전체').props.onClick();h.render();assert.deepEqual(Array.from(headingYears(h)),['2021','2023','2024']);
});
test('zero and loss are displayed; missing prior year never becomes a false growth rate',()=>{
  const h=earnings([f('2021','매출액',50),f('2023','매출액',0),f('2024','매출액',-5)]);h.render();
  const bars=elements(h.tree,n=>n.props?.className==='mk-earnings-bars')[0].props.children;
  assert.equal(text(bars[0].props.children[0]),'자료 없음');
  assert.equal(text(bars[1].props.children[0]),'0');assert.equal(text(bars[1].props.children[1]),'전년 자료 없음');
  assert.equal(elements(bars[1],n=>n.type==='i')[0].props.style.height,'0%');
  assert.equal(text(bars[2].props.children[0]),'-5');assert.equal(text(bars[2].props.children[1]),'음수 전환');
});
test('estimates, separate statements and unrelated accounts cannot extend actual annual window',()=>{
  const h=earnings([f('2023'),f('2024'),f('2026','매출액',10,{estimate:true}),f('2025','매출액',10,{basis:'OFS'}),f('2027','자본',10)]);
  const html=h.render();assert.deepEqual(Array.from(headingYears(h)),['2022','2023','2024']);assert.doesNotMatch(html,/>202[567]</);
});
test('unknown annual labels use honest observation counts instead of claiming recent fiscal years',()=>{
  const h=earnings([f('FY2021'),f('FY2023'),f('FY2024')]),html=h.render();
  assert.ok(button(h,'3개 기간'));assert.doesNotMatch(html,/회계연도까지|최근 3년/);
});
test('different real periods in the same fiscal year retain both reported amounts and suppress ambiguous YoY',()=>{
  const input=[f('2023','매출액',80),f('2024','매출액',100),f('2024','매출액',40,{period_start:'2024-04-01'})],before=JSON.stringify(input);
  const h=earnings(input),html=h.render(),headings=Array.from(headingYears(h));
  assert.ok(headings.includes('2024 · 2024-01-01 ~ 2024-12-31'));assert.ok(headings.includes('2024 · 2024-04-01 ~ 2024-12-31'));
  assert.match(html,/>100</);assert.match(html,/>40</);assert.match(html,/기간 다름/);assert.doesNotMatch(html,/\+25\.0%/);assert.equal(JSON.stringify(input),before);
});
test('an unverified account does not add an empty annual column or block another verified account growth',()=>{
  const unknown={period_start:null,period_end:null,period_verified:false};
  const h=earnings([f('2024','매출액',100),f('2025','매출액',150),
    f('2024','당기순이익',20,unknown),f('2025','당기순이익',30,unknown)]);h.render();
  assert.deepEqual(Array.from(headingYears(h)),['2023','2024','2025']);
  assert.doesNotMatch(text(h.tree),/2024 · 기간 미확인|2025 · 기간 미확인/);
  assert.match(text(h.tree),/50\.0%/);
  const net=elements(h.tree,n=>n.type==='tr').find(n=>text(n.props.children[0])==='당기순이익 증감률');
  assert.match(text(net),/기간 미확인/);
});
test('an actual date without verified context cannot supply the display period of an unknown account',()=>{
  for(const context of [{period_verified:false},{period_verified:true,period_reason:'conflicting_period_contexts'}]){
    const h=earnings([f('2024','매출액',100,context),f('2024','당기순이익',20,{period_start:null,period_end:null,period_verified:false})]);
    const html=h.render(),headings=Array.from(headingYears(h));
    assert.ok(headings.includes('2024 · 기간 미확인'));assert.ok(headings.includes('2024 · 2024-01-01 ~ 2024-12-31'));
    assert.match(html,/>100</);assert.match(html,/>20</);assert.doesNotMatch(html,/\+\d+\.\d%/);
  }
});
test('same actual period picks the latest publication in UTC without discarding another real period',()=>{
  const h=earnings([f('2024','매출액',100,{available_at:'2025-03-03T10:00:00+09:00'}),f('2024','매출액',120,{available_at:'2025-03-03T02:00:00Z'}),f('2024','매출액',90,{available_at:'2025-03-03T01:30:00+09:00'})]);
  const html=h.render();assert.deepEqual(Array.from(headingYears(h)),['2022','2023','2024']);assert.match(html,/>120</);assert.doesNotMatch(html,/>100<|>90</);
});
const point=(date,reference_price,comparison_eligible=true)=>({date,reference_price,comparison_eligible});
test('comparison range excludes invalid dates and discloses interior gaps without changing points',()=>{
  const points=[point('2024-01-01',100,false),point('2024-02-01',100),point('2024-03-01',null),point('2024-04-01',200),point('2024-05-01',null)];
  const before=JSON.stringify(points);
  assert.deepEqual(native(periods.comparisonWindow(points)),{start:'2024-02-01',end:'2024-04-01',gaps:true});
  assert.equal(JSON.stringify(points),before);
});
test('single, missing and invalid reference ranges never invent a comparison period',()=>{
  assert.deepEqual(native(periods.comparisonWindow([point('2024-01-01',null),point('2024-02-01',10)])),{start:'2024-02-01',end:'2024-02-01',gaps:false});
  assert.equal(periods.comparisonWindow([point('2024-01-01',10,false),point('2024-02-01',null),point('2024-03-01',NaN)]),null);
  assert.equal(periods.comparisonWindow([]),null);
});

function valuation(response,overrides={}){
  const selection={metric:'EPS',basis:'auto',years:5,multiple:'15',mode:'as_known',frequency:'auto',interval:'week',from:'',to:''};
  const key=JSON.stringify([response.code,'EPS','auto',5,'as_known','auto','week','','']);
  const h=hooks({0:selection,1:true,2:response,3:key,10:[response.metric_definition],...overrides});
  let dynamicIndex=0,blob=null;
  class FixtureURL extends URL{static createObjectURL(value){blob=value;return 'blob:fixture';}static revokeObjectURL(){}}
  const Component=load('valuation-chart.tsx',{
    react:h.react,'./types':types,'./analysis-periods':periods,'./valuation-series':load('valuation-series.ts'),'./chart-selection':load('chart-selection.ts'),'./price-history':load('price-history.ts'),'./market-section-boundary':{default:'fixture-boundary'},'./market-history':{writeMarketHistory(){}},'next/dynamic':{default:()=>dynamicIndex++===0?'fixture-chart':'fixture-eps'},
  },{URL:FixtureURL,URLSearchParams,Blob,setTimeout:()=>0,localStorage:{setItem(){}},location:{href:'https://example.invalid/stocks?code=000001'},window:{dispatchEvent(){}},Event:class{},document:{createElement:()=>({click(){}})}}).default;
  return {tree:null,render(){h.reset();this.tree=Component({code:response.code});},get blob(){return blob;}};
}
function analysis(){return {code:'000001',metric:'EPS',metric_definition:{code:'EPS',label:'기본 EPS',group:'이익',supports_ttm:true},share_basis:{per_share_basis_end:'2024-04-01'},multiple:15,basis:'CFS',frequency:'annual',summary:{representative_multiple:null},availability:{comparison:true,reasons:[]},ttm:{rows:[]},periods:[],series:[
  {...point('2024-01-01',null,false),metric_value:10,price:100},
  {...point('2024-02-01',150),metric_value:10,price:200},
  {...point('2024-03-01',null,false),metric_value:null,price:300},
  {...point('2024-04-01',300),metric_value:20,price:400},
  {...point('2024-05-01',null,false),metric_value:30,price:500},
]};}
const chart=h=>elements(h.tree,n=>n.type==='fixture-chart')[0];
test('actual valuation chart displays only eligible range; factor changes preserve date/range and CSV values',async()=>{
  const response=analysis(),before=JSON.stringify(response),h=valuation(response);h.render();
  const caption=()=>text(elements(h.tree,n=>n.props?.['aria-label']==='비교 가능한 실적 구간')[0]);
  assert.equal(caption(),'비교 가능한 구간 2024-02-01 ~ 2024-04-01 · 중간 공백 있음');
  assert.deepEqual(Array.from(chart(h).props.comparisons[0].values,p=>p.value),[null,150,null,300,null]);
  const rangeKey=chart(h).props.rangeKey;
  chart(h).props.onSelect(1);h.render();
  elements(h.tree,n=>n.props?.['aria-label']==='이익 비교 배수')[0].props.onChange({target:{value:'20'}});h.render();
  assert.equal(chart(h).props.rangeKey,rangeKey);assert.equal(caption(),'비교 가능한 구간 2024-02-01 ~ 2024-04-01 · 중간 공백 있음');
  const reading=elements(h.tree,n=>n.props?.['aria-label']==='선택한 날짜의 숫자')[0];assert.equal(text(reading.props.children[0]),'2024-02-01');
  assert.deepEqual(Array.from(chart(h).props.comparisons[0].values,p=>p.value),[null,200,null,400,null]);
  elements(h.tree,n=>n.type==='details'&&text(n.props.children[0])==='날짜별 숫자 보기')[0].props.onToggle({currentTarget:{open:true}});h.render();
  button(h,'CSV 저장').props.onClick();const csv=await h.blob.text();
  assert.match(csv,/"2024-02-01","200","10","20","200"/);assert.match(csv,/"2024-05-01","500","30","20",""/);
  assert.equal(JSON.stringify(response),before);
});
test('server-ineligible positive facts and negative metrics do not produce a range label or comparison values',()=>{
  const response=analysis();response.series=response.series.map((p,i)=>({...p,comparison_eligible:i===1,metric_value:i===1?-10:p.metric_value}));
  const h=valuation(response);h.render();assert.equal(elements(h.tree,n=>n.props?.['aria-label']==='비교 가능한 실적 구간').length,0);
  assert.deepEqual(Array.from(chart(h).props.comparisons[0].values,p=>p.value),[null,null,null,null,null]);
});
test('valuation distinguishes requested, displayed, stored and comparable ranges without claiming full collection',()=>{
  const response=analysis();response.history_coverage={requested:{start:'2021-05-01',end:'2024-05-01'},displayed:{start:'2024-01-01',end:'2024-05-01',rows:5},stored:{start:'2023-01-01',end:'2024-05-01',rows:50},collection:{status:'partial',backfill_complete:false,older_pages_pending:true,gap_pending:false}};
  const h=valuation(response);h.render();const details=elements(h.tree,n=>n.type==='details'&&text(n.props.children[0])==='가격 자료 범위')[0],value=text(details);
  assert.match(value,/요청 2021-05-01 ~ 2024-05-01/);assert.match(value,/표시 2024-01-01 ~ 2024-05-01/);assert.match(value,/저장된 기간 2023-01-01 ~ 2024-05-01/);
  assert.match(value,/실적 비교 2024-02-01 ~ 2024-04-01 · 중간 공백 있음/);assert.match(value,/이전 가격 자료가 더 남아 있어요/);assert.doesNotMatch(value,/전체 확보|수집 완료/);
});
test('switching to actual multiples retains dates, range key and user scenario; CSV separates the two multiples',async()=>{
  const response=analysis();response.series=response.series.map(p=>({...p,multiple:20}));
  const h=valuation(response,{19:[{year:3,metric_value:100,reference_price:1500}]});h.render();
  chart(h).props.onSelect(1);h.render();const rangeKey=chart(h).props.rangeKey;
  button(h,'배수 흐름').props.onClick();h.render();
  assert.equal(chart(h).props.rangeKey,rangeKey);
  assert.deepEqual(Array.from(chart(h).props.rows,p=>p.valid),[false,true,false,true,false]);
  assert.equal(chart(h).props.rows[1].close,20);
  assert.equal(text(elements(h.tree,n=>n.props?.['aria-label']==='선택한 날짜의 숫자')[0].props.children[0]),'2024-02-01');
  assert.match(text(h.tree),/사용자 가정 · 배당 제외/);
  elements(h.tree,n=>n.type==='details'&&text(n.props.children[0])==='날짜별 숫자 보기')[0].props.onToggle({currentTarget:{open:true}});h.render();
  button(h,'CSV 저장').props.onClick();const csv=await h.blob.text();
  assert.match(csv,/'?선택 비교 배수/);assert.match(csv,/당시 실제 배수/);
  assert.match(csv,/"2024-02-01","200","10","15","150","20"/);
  assert.match(csv,/"2024-05-01","500","30","15","",""/);
  button(h,'실적·주가').props.onClick();h.render();assert.equal(chart(h).props.rows[1].close,200);
});
