import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';
const require=createRequire(import.meta.url);
function load(file,dependencies={}){const code=ts.transpileModule(fs.readFileSync(new URL(`../../components/market/${file}`,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;const ctx={exports:{},Error,require:name=>dependencies[name]??require(name)};vm.runInNewContext(code,ctx);return ctx.exports;}
const selection=load('chart-selection.ts'),history=load('price-history.ts'),period=load('valuation-series.ts'),financial=load('financial-change.ts',{'./valuation-series':period});
const native=value=>JSON.parse(JSON.stringify(value));
test('a period/interval change selects the closest observed date, not a made-up or later date',()=>{
 assert.equal(selection.closestObservedIndex(['2026-01-02','2026-01-09'],'2026-01-03'),0);
 assert.equal(selection.closestObservedIndex(['2026-01-02','2026-01-09'],'2026-01-08'),1);
 assert.equal(selection.closestObservedIndex([], '2026-01-08'),null);assert.equal(selection.closestObservedIndex(['2026-01-02'],null),null);
 assert.deepEqual(native(selection.preservedLogicalRange(['2026-01-01','2026-01-02','2026-01-03'],['2026-01-02','2026-01-09'],{from:0,to:2})),{from:0,to:1});
 assert.equal(selection.preservedLogicalRange(['2025-01-01'],['2026-01-01'],{from:0,to:1}),null);
});
test('wide storage bounds do not imply terminal collection; real gaps and partial ranges are disclosed',()=>{
 const coverage={requested:{start:'2021-01-01',end:'2026-01-01'},stored:{start:'2024-01-01',end:'2026-01-01'},collection:{status:'partial',backfill_complete:false,older_pages_pending:true,gap_pending:false}};
 assert.equal(history.historyNotice(coverage),'이전 가격 자료가 더 남아 있어요.');
 assert.equal(history.historyNotice({...coverage,collection:{...coverage.collection,status:'collecting'}}),'이전 가격을 가져오는 중이에요.');
 assert.equal(history.historyNotice({...coverage,collection:{...coverage.collection,gap_pending:true,gap_status:'source_gap'}}),'중간에 확인되지 않은 가격 구간이 있어요.');
 assert.equal(history.historyNotice(undefined),null);
});
const fact=(year,value,extra={})=>({name:'매출액',period:String(year),period_start:`${year}-01-01`,period_end:`${year}-12-31`,period_verified:true,frequency:'annual',basis:'CFS',unit:'억원',value,...extra});
test('financial YoY requires verified periods, same unit/company/accounting basis, and keeps original values',()=>{
 const current=fact(2025,120),prior=fact(2024,100),before=JSON.stringify([current,prior]);
 assert.equal(financial.financialYearChange(current,[prior]).label,'+20.0%');
 for(const extra of [{period_verified:false},{unit:'원'},{basis:'OFS'},{input_company_id:'other'},{period_start:'2024-04-01'}])assert.equal(financial.financialYearChange(current,[{...prior,...extra}]).percent,null);
 assert.equal(financial.financialYearChange({...current,period_verified:false},[prior]).label,'기간 미확인');
 assert.equal(financial.financialYearChange({...current,unit:undefined},[{...prior,unit:undefined}]).label,'단위 미확인');
 assert.equal(financial.financialYearChange({...current,period_reason:'conflicting_period_contexts'},[prior]).percent,null);
 assert.equal(JSON.stringify([current,prior]),before);
});
test('chunk recovery is explicitly bounded per release and never treats API errors as asset failures',()=>{
 const boundary=load('market-section-boundary.tsx'),values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
 assert.equal(boundary.claimChunkRecovery(storage,'v1'),true);assert.equal(boundary.claimChunkRecovery(storage,'v1'),false);assert.equal(boundary.claimChunkRecovery(storage,'v2'),true);
 assert.equal(boundary.isChunkFailure(new Error('Loading chunk 42 failed')),true);assert.equal(boundary.isChunkFailure(new Error('API 503')),false);
});
test('keyboard separator supports limits/reset and reverses the right panel direction',()=>{
 const jsx={jsx:(type,props)=>({type,props})},split=load('workspace-split.tsx',{'react/jsx-runtime':jsx});
 assert.equal(split.clampPanelWidth(180,200,360),200);assert.equal(split.clampPanelWidth(400,200,360),360);
 const values=[],pane=split.PaneSeparator({label:'목록',width:240,onWidth:value=>values.push(value),initial:240,min:200,max:360,container:'.fixture'});
 for(const key of ['ArrowRight','Home','End','Escape'])pane.props.onKeyDown({key,shiftKey:false,preventDefault(){}});
 assert.deepEqual(values,[256,200,360,240]);
 const right=split.PaneSeparator({label:'호가',width:280,onWidth:value=>values.push(value),initial:280,min:220,max:360,container:'.fixture',side:'right'});
 right.props.onKeyDown({key:'ArrowRight',shiftKey:true,preventDefault(){}});assert.equal(values.at(-1),232);
});
test('Escape closes the actual search popup and clears its active-descendant reference',()=>{
 const cells=['삼성전자',false,true,0,'stocks'];let slot=0;
 const react={useState(initial){const index=slot++;if(!(index in cells))cells[index]=initial;return [cells[index],value=>{cells[index]=typeof value==='function'?value(cells[index]):value;}];},useEffect(){},useRef:()=>({current:null})};
 const component=load('header-search.tsx',{react,'lucide-react':{Search:'fixture-search',X:'fixture-x'},'./use-stock-search':{useStockSearch:()=>({rows:[{code:'005930',name:'fixture_non_production',market:'KOSPI'}],busy:false,failed:false,retry(){}})},'./company-logo':{default:'fixture-logo'},'next/dynamic':{default:()=> 'fixture-research'},'./research-search.module.css':{default:{switcher:'',wide:''}}}).default;
 const all=tree=>Array.isArray(tree)?tree.flatMap(all):tree&&typeof tree==='object'?[tree,...all(tree.props?.children)]:[];
 const render=()=>{slot=0;return all(component({onOpen(){throw new Error('Escape must not navigate');}}));};
 let tree=render(),input=tree.find(node=>node.type==='input');
 assert.equal(input.props['aria-activedescendant'],'stock-option-0');assert.ok(tree.some(node=>node.props?.role==='listbox'));
 input.props.onKeyDown({key:'Escape',nativeEvent:{},stopPropagation(){}});
 tree=render();input=tree.find(node=>node.type==='input');assert.equal(input.props['aria-expanded'],false);assert.equal(input.props['aria-activedescendant'],undefined);assert.ok(!tree.some(node=>node.props?.role==='listbox'));
});
