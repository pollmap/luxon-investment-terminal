import {test,expect,type Page} from '@playwright/test';

// Non-production fixtures verify requests and navigation, not financial coverage.
async function fixture(page:Page){
  const calls:string[]=[];const subscriptions=new Map<number,string[]>();let socketId=0;
  const stock={code:'005930',name:'삼성전자',market:'KOSPI',price:100,change:1,volume:10,value:1000,cap:10000,foreign:null,per:null,pbr:null,roe:null,dividend:null,foreign_net:null,institution_net:null,revenue:null,operating_profit:null,as_of:'2026-10-08T15:30:00+09:00',received_at:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',facts:{}};
  await page.routeWebSocket('**/api/market/v1/stream',ws=>{
    const id=++socketId;
    ws.onMessage(message=>{subscriptions.set(id,JSON.parse(String(message)).codes);});
    ws.onClose(()=>subscriptions.delete(id));
  });
  await page.route('**/api/market/v1/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace('/api/market/v1','');
    calls.push(path+url.search);
    let data:unknown={rows:[],status:'ready'};
    if(path==='/metrics')data={};
    else if(path==='/search-index')data={rows:[{code:stock.code,name:stock.name,market:stock.market,tokens:['005930','삼성전자','ㅅㅅㅈㅈ']}]};
    else if(path==='/screener')data={rows:[stock],total:1};
    else if(path==='/overview')data={indices:['KOSPI','KOSDAQ','KPI200'].map(code=>({code,price:100,change:1,market_status:'CLOSE'})),breadth:{up:1,down:0,flat:0},total:1,value:1000,sectors:[],filings:{rows:[],status:'ready'}};
    else if(path==='/securities/005930')data={security:stock,detail:{financials:[],flows:[]},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
    else if(path==='/securities/005930/quote')data=stock;
    else if(path.endsWith('/chart'))data={status:'ready',return_pct:1,points:[{time:'20261008090000',value:100},{time:'20261008150000',value:101}]};
    await route.fulfill({json:data});
  });
  return {calls,subscriptions,count:(path:string)=>calls.filter(value=>value===path).length};
}

test('index day responses are shared, closed detail and hidden pages stop polling',async({page})=>{
  await page.clock.install();const state=await fixture(page);
  await page.goto('/stocks');
  const kospi='/indices/KOSPI/chart?period=day';
  await expect.poll(()=>state.count(kospi)).toBe(1);
  await page.getByRole('button',{name:'코스피 차트 보기',exact:true}).click();
  await expect(page.getByRole('heading',{name:'코스피의 흐름'})).toBeVisible();
  expect(state.count(kospi)).toBe(1);
  await page.getByRole('group',{name:'지수 차트 기간'}).getByRole('button',{name:'1주',exact:true}).click();
  await expect.poll(()=>state.count('/indices/KOSPI/chart?period=week')).toBe(1);
  await page.getByRole('button',{name:'상세 차트 접기 ↑'}).click();
  await page.clock.fastForward(120100);
  await expect.poll(()=>state.count(kospi)).toBe(2);
  expect(state.count('/indices/KOSPI/chart?period=week')).toBe(1);
  await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}));
  await page.clock.fastForward(120100);
  expect(state.count(kospi)).toBe(2);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  await expect.poll(()=>state.count(kospi)).toBe(3);
});

test('detail pauses the hidden market stream and restores the original list on return',async({page})=>{
  const state=await fixture(page);await page.goto('/stocks');
  await expect(page.locator('.mk-stock-link').first()).toBeVisible();
  await expect.poll(()=>state.subscriptions.size).toBe(1);
  const overviewCount=state.count('/overview');
  await page.locator('.mk-stock-link').first().click();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expect.poll(()=>state.subscriptions.size).toBe(1);
  expect([...state.subscriptions.values()]).toEqual([['005930']]);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  expect(state.count('/overview')).toBe(overviewCount);
  await page.getByRole('button',{name:'목록으로 돌아가기'}).click();
  await expect(page.locator('.mk-stock-link').first()).toContainText('삼성전자');
  await expect.poll(()=>state.subscriptions.size).toBe(1);
});

test('header and keyboard search reuse the identity index without live search calls',async({page})=>{
  const state=await fixture(page);await page.goto('/stocks');
  await expect.poll(()=>state.count('/search-index')).toBe(1);
  await page.getByRole('combobox',{name:'종목명 또는 코드 검색'}).fill('ㅅㅅㅈㅈ');
  await expect(page.getByRole('listbox',{name:'검색 결과'}).getByRole('option')).toContainText('삼성전자');
  await page.keyboard.press('Escape');await page.keyboard.press('Control+k');
  await page.getByRole('dialog').getByRole('textbox',{name:'종목명 또는 코드'}).fill('005930');
  await expect(page.getByRole('dialog').locator('.mk-search-result')).toContainText('삼성전자');
  expect(state.count('/search-index')).toBe(1);expect(state.calls.some(path=>path.startsWith('/search?'))).toBe(false);
});

test('failed metric or basis changes never reuse another selection as verified analysis',async({page})=>{
  await fixture(page);let fail=false;
  const metrics=[{code:'EPS',label:'기본 EPS',group:'이익',default_multiple:15,supports_ttm:true},
    {code:'OCF',label:'영업현금흐름/주',group:'현금흐름',default_multiple:null,supports_ttm:true}];
  await page.route('**/analysis/metrics',route=>route.fulfill({json:{metrics}}));
  await page.route('**/analysis/valuation?**',async route=>{
    if(fail)return route.fulfill({status:503,json:{error:'fixture_unavailable'}});
    const query=new URL(route.request().url()).searchParams,metric=query.get('metric')||'EPS';
    const value=metric==='EPS'?10:20;
    return route.fulfill({json:{code:'005930',metric,metric_definition:metrics.find(row=>row.code===metric),
      multiple:15,basis:query.get('basis')==='OFS'?'OFS':'CFS',frequency:'ttm',
      series:[{date:'2026-10-07',price:100,metric_value:value,reference_price:value*15,historical_reference:null,multiple:100/value,period_end:'2026-06-30',comparison_eligible:true}],
      periods:[],summary:{representative_multiple:12,monthly_observations:24},availability:{comparison:true,reasons:[]},
      ttm:{rows:[{account_id:'fixture',name:'검증된 시험 실적',value:100,unit:'원',period_end:'2026-06-30'}]}}});
  });
  await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주'));
  const chart=page.getByRole('region',{name:'실적과 주가',exact:true});
  await expect(chart.getByLabel('선택한 날짜의 숫자')).toContainText('150');
  fail=true;
  await chart.getByRole('combobox',{name:'가치평가 지표'}).selectOption('OCF');
  await expect(chart.getByRole('alert')).toContainText('불러오지 못했습니다');
  await expect(chart).not.toContainText('선택한 기간에 비교할 수 있는 실적이 없어요.');
  await expect(chart.getByLabel('선택한 날짜의 숫자')).not.toContainText('150');
  await chart.getByText('최근 12개월 실적 · TTM',{exact:true}).click();
  await expect(chart).not.toContainText('검증된 시험 실적');
  fail=false;await chart.getByRole('button',{name:'다시 시도',exact:true}).click();
  await expect(chart.getByLabel('선택한 날짜의 숫자')).toContainText('300');
  await expect(chart.getByRole('alert')).toHaveCount(0);
  await chart.getByText('비교 기준·직접 기간 선택',{exact:true}).click();
  fail=true;await chart.getByRole('combobox',{name:'가치평가 재무 기준'}).selectOption('OFS');
  await expect(chart.getByRole('alert')).toBeVisible();
  await expect(chart.getByLabel('선택한 날짜의 숫자')).not.toContainText('300');
  await expect(chart).not.toContainText('검증된 시험 실적');
  await expect(chart.locator('.mk-section-head')).not.toContainText('연결');
});
