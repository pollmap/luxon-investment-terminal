import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Fixture-only chart adapter: exercise the component's real React effects.
function harness(){
  const refs=[],effects=[],pending=[],series=[],removed=[];
  let index=0,effectIndex=0,range=null,fits=0,created=0;
  const host={closest:()=>null};
  const scale={getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>{range={...r};},fitContent:()=>{fits++;range={from:0,to:3};}};
  const chart={addSeries(type,options){const s={type,options,data:[],setData(data){this.data=data;},applyOptions(o){this.options={...this.options,...o};},priceScale:()=>({applyOptions(){}})};series.push(s);return s;},removeSeries:s=>removed.push(s),timeScale:()=>scale,applyOptions(){},subscribeCrosshairMove(){},remove(){}};
  const react={useRef(value){return refs[index++]??=( {current:value} );},useEffect(fn,deps){const i=effectIndex++;if(!effects[i]||deps.some((v,n)=>v!==effects[i].deps[n]))pending.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()};});}};
  const lib={createChart:()=>{created++;return chart;},ColorType:{Solid:0},LineStyle:{Solid:0,Dashed:1},LineType:{Simple:0,WithSteps:1}};
  for(const key of ['LineSeries','CandlestickSeries','BarSeries','HistogramSeries','AreaSeries'])lib[key]=key;
  const source=fs.readFileSync(new URL('../../components/market/financial-chart.tsx',import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const selection={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../../components/market/chart-selection.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,selection);
  const context={exports:{},require(name){if(name==='react')return react;if(name==='lightweight-charts')return lib;if(name==='react/jsx-runtime')return {jsx:()=>null};if(name==='./chart-selection')return selection.exports;throw Error(name);},getComputedStyle:()=>({getPropertyValue:()=>''}),MutationObserver:class{observe(){}disconnect(){}},matchMedia:()=>({addEventListener(){},removeEventListener(){}})};
  vm.runInNewContext(code,context);
  return {series,removed,scale,get fits(){return fits;},get created(){return created;},render(props){index=0;effectIndex=0;context.exports.default(props);refs[0].current=host;for(const fn of pending.splice(0))fn();}};
}
const rows=['2026-01-01','2026-01-02','2026-01-03','2026-01-04'].map((time,i)=>({time,close:100+i}));
const comparison=(values,extra={})=>({values:values.map((value,i)=>({time:rows[i].time,value})),color:'#123456',...extra});

test('multiple changes update existing segments, preserve gaps and user zoom',()=>{
  const h=harness();
  h.render({rows,comparisons:[comparison([10,20,null,40],{area:true})]});
  const overlays=h.series.slice(3);
  assert.equal(overlays.length,2);
  assert.deepEqual(Array.from(overlays[0].data,p=>p.value),[10,20]);
  assert.deepEqual(Array.from(overlays[1].data,p=>p.value),[40]);
  h.scale.setVisibleLogicalRange({from:1,to:2});
  h.render({rows,comparisons:[comparison([20,40,null,80],{area:true})]});
  assert.equal(h.created,1);assert.equal(h.series.length,5);assert.equal(h.removed.length,0);
  assert.equal(overlays[1].data[0].value,80);
  assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:1,to:2});assert.equal(h.fits,1);
});
test('changed gap topology removes surplus segments and never bridges missing values',()=>{
  const h=harness();h.render({rows,comparisons:[comparison([10,null,30,40])]});
  const old=h.series.slice(3);
  h.render({rows,comparisons:[comparison([null,20,30,null])]});
  assert.deepEqual(Array.from(old[0].data,p=>p.value),[20,30]);
  assert.equal(h.removed[0],old[1]);
  h.render({rows,comparisons:[]});assert.equal(h.removed[1],old[0]);
});
test('area to line replaces incompatible series and applies dash/color changes',()=>{
  const h=harness();h.render({rows,comparisons:[comparison([10,20,30,40],{area:true})]});
  const old=h.series[3];
  h.render({rows,comparisons:[comparison([10,20,30,40],{dashed:true})]});
  assert.equal(h.removed[0],old);assert.equal(h.series[4].type,'LineSeries');assert.equal(h.series[4].options.lineStyle,1);
  h.render({rows,comparisons:[comparison([10,20,30,40],{color:'#abcdef'})]});
  assert.equal(h.series.length,5);assert.equal(h.series[4].options.color,'#abcdef');assert.equal(h.series[4].options.lineStyle,0);
});

test('new bars and quote changes do not reset the viewed range',()=>{
  const h=harness();h.render({rows});h.scale.setVisibleLogicalRange({from:1,to:2});
  h.render({rows:[...rows,{time:'2026-01-05',close:105}]});
  assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:1,to:2});
  assert.equal(h.fits,1);assert.equal(h.created,1);
});
test('loading earlier history anchors the same dates, not their previous indices',()=>{
  const h=harness();h.render({rows});h.scale.setVisibleLogicalRange({from:1.5,to:2.5});
  h.render({rows:[{time:'2025-12-31',close:99},...rows]});
  assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:2.5,to:3.5});assert.equal(h.fits,1);
});
test('line, candle and OHLC switch the price series without recreating the chart',()=>{
  const candles=rows.map(row=>({...row,open:100,high:105,low:95}));
  const h=harness();h.render({rows:candles});h.scale.setVisibleLogicalRange({from:1,to:2});
  for(const kind of ['candle','bar','area','line']){
    h.render({rows:candles,kind});assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:1,to:2});
  }
  assert.equal(h.created,1);assert.equal(h.fits,1);assert.equal(h.removed.length,4);
});
test('explicit period change or a new non-overlapping session fits once',()=>{
  const h=harness();h.render({rows,rangeKey:'005930:1year'});
  h.scale.setVisibleLogicalRange({from:1,to:2});
  h.render({rows,rangeKey:'005930:5years'});assert.equal(h.fits,2);
  h.render({rows:rows.map(row=>({...row,time:row.time.replace('2026','2027')})),rangeKey:'005930:5years'});
  assert.equal(h.fits,3);
});

test('unverified and nonfinite line values are whitespace, never zero or a made-up ratio',()=>{
  const h=harness();h.render({rows:[{time:'2026-01-01',close:10},{time:'2026-01-02',close:20,valid:false},{time:'2026-01-03',close:NaN},{time:'2026-01-04',close:0}]});
  const line=h.series[0];assert.deepEqual(Array.from(line.data,p=>p.value),[10,undefined,undefined,0]);
  h.scale.setVisibleLogicalRange({from:1,to:2});
  h.render({rows:[{time:'2026-01-01',close:100},{time:'2026-01-02',close:NaN,valid:false},{time:'2026-01-03',close:NaN},{time:'2026-01-04',close:5}]});
  assert.equal(h.created,1);assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:1,to:2});
});

test('breakGaps gives each contiguous main-line segment its own series without changing dates or zoom',()=>{
  const gaps=rows.map((row,i)=>({...row,close:[10,NaN,0,-5][i]}));
  const h=harness();h.render({rows:gaps,breakGaps:true,rangeKey:'same'});
  assert.deepEqual(Array.from(h.series[0].data,p=>p.time),rows.map(r=>r.time));
  assert.ok(h.series[0].data.every(p=>p.value===undefined));
  const segments=h.series.slice(3);assert.equal(segments.length,2);
  assert.deepEqual(Array.from(segments[0].data,p=>p.value),[10]);
  assert.deepEqual(Array.from(segments[1].data,p=>p.value),[0,-5]);
  assert.ok(segments.every(s=>s.options.lineType===0));
  h.scale.setVisibleLogicalRange({from:1,to:2});
  h.render({rows:gaps.map((r,i)=>({...r,close:i===0?NaN:r.close})),breakGaps:true,rangeKey:'same'});
  assert.equal(h.removed[0],segments[1]);assert.deepEqual(Array.from(segments[0].data,p=>p.value),[0,-5]);
  assert.equal(h.created,1);assert.equal(h.fits,1);assert.deepEqual(h.scale.getVisibleLogicalRange(),{from:1,to:2});
  h.render({rows,breakGaps:false,rangeKey:'same'});
  assert.equal(h.removed[1],segments[0]);assert.deepEqual(Array.from(h.series[0].data,p=>p.value),[100,101,102,103]);
});
