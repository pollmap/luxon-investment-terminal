import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
function load(file,dependencies={}) {
  const source=fs.readFileSync(new URL(`../../components/market/${file}`,import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const context={exports:{},Intl,Date,require:name=>dependencies[name]??require(name)};
  vm.runInNewContext(code,context);
  return context.exports;
}
const calendar=load('market-calendar.ts');
const {formatCalendarTime:time,calendarSessions:view}=calendar;
// All timestamps below are fixtures; no provider or production calls.
const session=(start,end)=>({startTime:start,endTime:end});

test('equivalent explicit offsets display the same Korean time',()=>{
  for(const value of ['2026-03-25T09:00:00+09:00','2026-03-25T00:00:00Z','2026-03-24T20:00:00-04:00']) {
    assert.equal(time(value,'2026-03-25'),'09:00');
  }
  assert.equal(time('2026-03-25T00:00:00.123+00:00','2026-03-25'),'09:00');
});

test('US DST offsets and following Korean date are retained',()=>{
  assert.equal(time('2026-03-06T09:30:00-05:00','2026-03-06'),'23:30');
  assert.equal(time('2026-03-09T09:30:00-04:00','2026-03-09'),'22:30');
  assert.equal(time('2026-03-09T16:00:00-04:00','2026-03-09'),'03/10 05:00');
  assert.equal(time('2026-11-02T16:00:00-05:00','2026-11-02'),'11/03 06:00');
});

test('year boundary and previous Korean date are explicit',()=>{
  assert.equal(time('2026-12-31T16:00:00-05:00','2026-12-31'),'2027-01-01 06:00');
  assert.equal(time('2026-01-01T00:00:00+14:00','2026-01-01'),'2025-12-31 19:00');
  assert.equal(time('2026-03-25T15:00:00Z','2026-03-25'),'03/26 00:00');
});

test('missing timezone, invalid date and malformed input are never guessed',()=>{
  for(const value of [undefined,null,'','09:00','2026-03-25T09:00:00','2026-02-30T09:00:00Z','2026-03-25T24:00:00Z','2026-03-25T09:00:00+99:00',{}]) {
    assert.equal(time(value,'2026-03-25'),null);
  }
});

test('official KR schema supplies integrated only and nullable holiday',()=>{
  const spec=JSON.parse(fs.readFileSync(new URL('../../../../docs/toss-openapi-reference-20261008.json',import.meta.url),'utf8'));
  assert.deepEqual(Object.keys(spec.components.schemas.KrMarketDay.properties).sort(),['date','integrated']);
  assert.equal(view({date:'2026-05-05',integrated:null},'KR').status,'closed');
  assert.equal(view({date:'2026-05-05'},'KR').status,'unavailable');
  assert.equal(view(undefined,'KR').status,'unavailable');
});

test('only supplied sessions appear; partial holiday does not synthesize hours',()=>{
  const result=view({date:'2026-03-25',integrated:{preMarket:null,regularMarket:session('2026-03-25T00:00:00Z','2026-03-25T06:30:00Z'),afterMarket:null}},'KR');
  assert.equal(result.status,'ready');
  assert.equal(result.rows.length,1);
  assert.equal(result.rows[0].time,'09:00–15:30');
  assert.equal(result.rows[0].label,'정규장');
});

test('explicit empty holiday differs from missing or malformed session data',()=>{
  assert.equal(view({date:'2026-03-25',dayMarket:null,preMarket:null,regularMarket:null,afterMarket:null},'US').status,'closed');
  assert.equal(view({date:'2026-03-25'},'US').status,'unavailable');
  assert.equal(view({date:'2026-03-25',integrated:{}},'KR').status,'unavailable');
  const result=view({date:'2026-03-25',integrated:{regularMarket:session('bad','2026-03-25T09:00:00Z')}},'KR');
  assert.equal(result.rows[0].time,'시간 확인 불가');
});

test('reversed times do not become a plausible trading interval',()=>{
  const result=view({date:'2026-03-25',integrated:{regularMarket:session('2026-03-25T16:00:00+09:00','2026-03-25T09:00:00+09:00')}},'KR');
  assert.equal(result.rows[0].time,'시간 확인 불가');
});

test('component labels market dates separately from Korean session times',()=>{
  const {CalendarTable}=load('market-reference.tsx',{'./market-calendar':calendar,'./use-visible-query':{}});
  const {renderToStaticMarkup}=require('react-dom/server');
  const html=renderToStaticMarkup(CalendarTable({calendars:{
    KR:{calendar:{today:{date:'2026-03-25',integrated:null}}},
    US:{calendar:{today:{date:'2026-03-25',regularMarket:session('2026-03-25T09:30:00-04:00','2026-03-25T16:00:00-04:00')}}},
  }}));
  assert.match(html,/국내 · 통합/);
  assert.match(html,/기준일 · 현지/);
  assert.match(html,/거래시간 · 한국 시각/);
  assert.match(html,/정규장 22:30–03\/26 05:00/);
  assert.doesNotMatch(html,/<select|KRX|NXT/);
});
