import test from 'node:test';
import assert from 'node:assert/strict';
import {chartCutoff,intradayWindow} from '../../components/market/chart-period.ts';

test('saved calendar ranges preserve month end and leap dates',()=>{
 assert.equal(chartCutoff('2026-10-02',1260),'2021-10-02');
 assert.equal(chartCutoff('2024-02-29',252),'2023-02-28');
 assert.equal(chartCutoff('2026-03-31',21),'2026-02-28');
 assert.equal(chartCutoff('2026-10-02',100000),'');
});
test('day and week select distinct observed sessions without invented bars',()=>{
 const rows=['2026-09-21','2026-09-28','2026-10-02'].map(date=>({date,time:date+'T09:00:00+09:00'}));
 assert.deepEqual(intradayWindow(rows,'day'),[rows[2]]);
 assert.deepEqual(intradayWindow(rows,'week'),[rows[1],rows[2]]);
 assert.equal(rows.length,3);
});
test('bad time values are omitted and a year boundary does not lose observations',()=>{
 const rows=['2025-12-30','2026-01-02'].map(date=>({date,time:date+'T09:00:00+09:00'}));
 assert.deepEqual(intradayWindow([{date:'invalid',time:'invalid'},...rows],'week'),rows);
 assert.deepEqual(intradayWindow([],'day'),[]);
});
