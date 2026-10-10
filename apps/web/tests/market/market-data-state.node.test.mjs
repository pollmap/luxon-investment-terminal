import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
const {renderToStaticMarkup}=require('react-dom/server');
function load(file,dependencies={},append=''){
  const source=fs.readFileSync(new URL(`../../components/market/${file}`,import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const context={exports:{},require:name=>dependencies[name]??require(name)};
  vm.runInNewContext(code+append,context);
  return context.exports;
}
const state=load('market-data-state.ts');
// Synthetic rows and hook lifecycle only: no production or provider access.
const row=(code,rank,price=rank*100)=>({code,name:code,rank,price,change:1,value:1000,volume:10});
const initial=()=>({status:'ready',as_of:'2026-10-08T09:00:00+09:00',scope:'시장 전체',change_basis:'전일 종가',rows:[row('005930',1),row('000660',2)]});
function harness(component,data){
  const cells=[],effects=[],pending=[];let slot=0,dirty=false,result={data,error:false},retries=0,path='';
  const react={
    useState(value){const i=slot++;if(!(i in cells))cells[i]=value;return [cells[i],next=>{const value=typeof next==='function'?next(cells[i]):next;if(!Object.is(value,cells[i])){cells[i]=value;dirty=true;}}];},
    useEffect(fn,deps){const i=slot++;if(!effects[i]||deps.some((v,n)=>v!==effects[i][n]))pending.push(()=>{effects[i]=deps;fn();});},
  };
  const components=load('market-data.tsx',{
    react,'./market-data-state':state,'./types':{format:value=>String(value??'—'),tone:()=>''},
    './use-visible-query':{useVisibleQuery:p=>{path=p;return {...result,retry:()=>{retries++;}};}},
    './market-pulse':{},'./market-reference':{},
  },'\nexports.MarketRankings=MarketRankings;exports.MarketInvestors=MarketInvestors;');
  const h={tree:null,update(data,error=false){result={data,error};},render(){
    for(let i=0;i<5;i++){dirty=false;slot=0;h.tree=components[component]({onSelect:()=>{}});for(const fn of pending.splice(0))fn();if(!dirty)return renderToStaticMarkup(h.tree);}
    throw new Error('fixture component did not settle');
  },get retries(){return retries;},get path(){return path;}};
  return h;
}
function elements(tree,predicate){
  if(Array.isArray(tree))return tree.flatMap(node=>elements(node,predicate));
  if(!tree||typeof tree!=='object')return [];
  return [...(predicate(tree)?[tree]:[]),...elements(tree.props?.children,predicate)];
}
function text(tree){if(Array.isArray(tree))return tree.map(text).join('');if(tree==null||typeof tree==='boolean')return '';return typeof tree==='object'?text(tree.props.children):String(tree);}
const button=(h,label)=>elements(h.tree,n=>n.type==='button'&&text(n)===label)[0];
const rows=h=>elements(h.tree,n=>n.type==='tbody')[0].props.children.map(n=>text(elements(n,n=>n.type==='button')[0]));

test('latest metadata and row values change while saved ranks and order remain stable',()=>{
  const h=harness('MarketRankings',initial());h.render();
  h.update({...initial(),as_of:'2026-10-08T09:01:00+09:00',rows:[row('000660',1,210),row('005930',2,110)]});
  const html=h.render();assert.deepEqual(rows(h),['005930','000660']);
  assert.match(html,/2026-10-08 09:01/);assert.doesNotMatch(html,/2026-10-08(?:T| )09:00/);assert.match(html,/<td>110<\/td>/);
  const ranks=elements(h.tree,n=>n.type==='tbody')[0].props.children.map(n=>n.props.children[0].props.children);
  assert.deepEqual(ranks,[1,2]);
  button(h,'최신 순위 반영').props.onClick();h.render();
  assert.deepEqual(rows(h),['000660','005930']);assert.equal(h.retries,1);
});

test('stale ranking stays readable and retry never reorders the rows',()=>{
  const h=harness('MarketRankings',initial());h.render();
  h.update({...initial(),status:'stale',rows:[row('000660',1),row('005930',2)]});
  const html=h.render();assert.match(html,/갱신하지 못했어요. 마지막 자료예요/);assert.deepEqual(rows(h),['005930','000660']);
  button(h,'다시 시도').props.onClick();h.render();assert.equal(h.retries,1);assert.deepEqual(rows(h),['005930','000660']);
});

test('network failure preserves existing rows with explicit recovery',()=>{
  const h=harness('MarketRankings',initial());h.render();h.update(initial(),true);
  assert.match(h.render(),/마지막 자료예요/);assert.deepEqual(rows(h),['005930','000660']);assert.ok(button(h,'다시 시도'));
});

test('successful empty response clears previous rows and date; later data starts a new order',()=>{
  const h=harness('MarketRankings',initial());h.render();h.update({status:'ready',rows:[],as_of:null});
  const html=h.render();assert.match(html,/표시할 종목이 없어요/);assert.doesNotMatch(html,/005930|000660|2026-10-08|<time/);
  h.update({...initial(),rows:[row('000660',1),row('005930',2)]});h.render();assert.deepEqual(rows(h),['000660','005930']);
});

test('different ranking selection cannot inherit frozen rows and its selections survive stale retry',()=>{
  const h=harness('MarketRankings',initial());h.render();
  elements(h.tree,n=>n.type==='select')[0].props.onChange({target:{value:'TOP_GAINERS'}});
  h.update(null);assert.doesNotMatch(h.render(),/005930|000660/);assert.match(h.path,/type=TOP_GAINERS/);
  h.update({...initial(),status:'stale',rows:[row('000660',1)]});h.render();
  button(h,'다시 시도').props.onClick();h.render();assert.match(h.path,/type=TOP_GAINERS&duration=1d&exclude_caution=false/);assert.deepEqual(rows(h),['000660']);
});

test('missing ranked members keep their exact slots with null values and an explicit state',()=>{
  const data=initial(),order=state.captureRankingOrder(null,'k',data);
  const view=state.rankingView({...data,rows:[row('000660',1,999)]},'k',order);
  assert.equal(view.rows.length,2);assert.equal(view.rows[0].code,'005930');assert.equal(view.rows[0].name,'005930');assert.equal(view.rows[0].rank,1);
  for(const key of ['price','change','value','volume'])assert.equal(view.rows[0][key],null);
  assert.equal(view.rows[0].unranked,true);
  assert.equal(view.rows[1].code,'000660');assert.equal(view.rows[1].price,999);assert.equal(view.rows[1].rank,2);assert.equal(view.rows[1].unranked,false);
  assert.equal(data.rows[0].price,100);
  const h=harness('MarketRankings',data);h.render();h.update({...data,rows:[row('000660',1,999)]});
  const html=h.render();assert.deepEqual(rows(h),['005930','000660']);assert.match(html,/순위에서 제외/);
  const missing=elements(h.tree,n=>n.type==='tbody')[0].props.children[0];
  assert.deepEqual(Array.from(missing.props.children.slice(2),text),['—','—','—','—']);
});

test('entirely replaced membership keeps every old slot until explicit refresh',()=>{
  const h=harness('MarketRankings',initial());h.render();h.update({...initial(),rows:[row('005380',1)]});
  const html=h.render();assert.deepEqual(rows(h),['005930','000660']);assert.equal((html.match(/순위에서 제외/g)||[]).length,2);assert.doesNotMatch(html,/표시할 종목이 없어요/);
  for(const row of elements(h.tree,n=>n.type==='tbody')[0].props.children)assert.deepEqual(Array.from(row.props.children.slice(2),text),['—','—','—','—']);
  button(h,'최신 순위 반영').props.onClick();h.render();assert.deepEqual(rows(h),['005380']);
});

test('market investor stale and network failures preserve table and offer retry',()=>{
  const data={status:'ready',rows:[{date:'2026-10-08',individual:{buy:300000000,sell:100000000,net:200000000}}]};
  const h=harness('MarketInvestors',data);h.render();
  elements(h.tree,n=>n.type==='select')[0].props.onChange({target:{value:'KOSDAQ'}});
  elements(h.tree,n=>n.type==='select')[1].props.onChange({target:{value:'1w'}});button(h,'매수').props.onClick();
  h.update({...data,status:'stale'});const html=h.render();
  assert.match(html,/시장 수급을 갱신하지 못했어요. 마지막 자료예요/);assert.match(html,/2026-10-08/);assert.match(html,/<td class="">3<\/td>/);
  button(h,'다시 시도').props.onClick();h.render();assert.equal(h.retries,1);assert.match(h.path,/KOSDAQ\/investors\?interval=1w/);
  h.update(data,true);assert.match(h.render(),/마지막 자료예요/);assert.match(h.render(),/2026-10-08/);
});

test('empty error shows retry rather than a successful empty or endless loading state',()=>{
  for(const component of ['MarketRankings','MarketInvestors']){
    const h=harness(component,{status:'error',rows:[]});const html=h.render();
    assert.match(html,/불러오지 못했어요/);assert.ok(button(h,'다시 시도'));assert.doesNotMatch(html,/가져오는 중|표시할 종목이 없어요|조회된 시장 수급이 없어요/);
  }
});
