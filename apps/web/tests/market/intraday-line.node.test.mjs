import test from 'node:test';
import assert from 'node:assert/strict';
import {intradayLinePoints,resolveLineInterval} from '../../components/market/intraday-line.ts';

test('five-minute line uses last actual observations and preserves the original records',()=>{
 const rows=['090100','090200','090400','090500','090900','091000'].map((time,i)=>({time:`20261002${time}`,value:100+i,source:'fixture'}));
 const before=JSON.stringify(rows);
 const selected=intradayLinePoints(rows,'5min');
 assert.deepEqual(selected,[rows[2],rows[4],rows[5]]);
 assert.strictEqual(selected[0],rows[2]);
 assert.equal(JSON.stringify(rows),before);
 assert.strictEqual(intradayLinePoints(rows,'1min'),rows);
});
test('gaps and date boundaries do not create an invented observation',()=>{
 const rows=[{time:'2026-10-02T23:59:00+09:00',value:5},{time:'2026-10-03T00:01:00+09:00',value:10},{time:'2026-10-03T00:04:00+09:00',value:15},{time:'2026-10-03T08:01:00+09:00',value:20}];
 assert.deepEqual(intradayLinePoints(rows,'5min'),[rows[0],rows[2],rows[3]]);
 const daily=[{time:'2026-10-02',value:10},{time:'2026-10-05',value:12}];
 assert.strictEqual(intradayLinePoints(daily,'5min'),daily);
});
test('shared and saved intervals take priority over initial device defaults',()=>{
 assert.equal(resolveLineInterval(undefined,undefined,true),'5min');
 assert.equal(resolveLineInterval(undefined,undefined,false),'1min');
 assert.equal(resolveLineInterval('1min','5min',true),'1min');
 assert.equal(resolveLineInterval(undefined,'1min',true),'1min');
 assert.equal(resolveLineInterval('invalid','5min',false),'5min');
});
