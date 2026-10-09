import {test,expect,type Page} from '@playwright/test';

// Fixture timings test dependency ordering, not production financial coverage or latency.
async function fixture(page:Page, options:{summaryDelay?:number;summaryError?:boolean;chartError?:boolean;chartDelay?:number;summaryGate?:Promise<void>}={}) {
  const calls:{path:string;start:number;end?:number}[]=[];
  const stock={code:'005930',name:'삼성전자',market:'KOSPI',price:100,change:1,volume:10,value:1000,cap:10000,foreign:null,per:null,pbr:null,roe:null,dividend:null,foreign_net:null,institution_net:null,revenue:null,operating_profit:null,as_of:'2026-10-08T15:30:00+09:00',received_at:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',facts:{}};
  await page.routeWebSocket('**/api/market/v1/stream',()=>{});
  await page.route('**/api/market/v1/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace('/api/market/v1','');
    const call:{path:string;start:number;end?:number}={path:path+url.search,start:Date.now()};calls.push(call);
    const chart=/\/(chart|bars|price-window)$/.test(path);
    let status=200,data:unknown={rows:[],status:'ready'};
    if(path==='/metrics')data={};
    else if(path==='/search-index')data={rows:[{code:stock.code,name:stock.name,market:stock.market,tokens:['005930','삼성전자']}]};
    else if(path==='/screener')data={rows:[stock],total:1};
    else if(path==='/securities/005930') {
      await options.summaryGate;
      await new Promise(resolve=>setTimeout(resolve,options.summaryDelay??600));
      status=options.summaryError?503:200;
      data={security:stock,detail:{financials:[],flows:[]},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
    } else if(path.endsWith('/quote'))data=stock;
    else if(chart) {
      await new Promise(resolve=>setTimeout(resolve,options.chartDelay??250));status=options.chartError?503:200;
      data=path.endsWith('/chart')?{status:'ready',session_date:'2026-10-07',data_as_of:'2026-10-07T15:30:00+09:00',points:[{time:'20261007090000',value:98},{time:'20261007153000',value:99}]}:
        {status:'ready',rows:[{date:'2026-10-07',time:'2026-10-07T15:25:00+09:00',open:98,high:101,low:97,close:99,volume:10,ma20:null,ohlc_valid:true}]};
    }
    call.end=Date.now();await route.fulfill({status,json:data});
  });
  return calls;
}

for(const [name,query,path] of [
  ['line','mode=intraday&kind=line&period=day','/securities/005930/chart?period=day'],
  ['candle','mode=intraday&kind=candle&period=day','/securities/005930/bars?interval=5min&venue=%ED%86%B5%ED%95%A9&adjustment=raw'],
  ['history','mode=history&kind=candle&range=1260&bar=week','/securities/005930/price-window?range=1260&interval=week'],
]) {
  test(`first ${name} chart starts before summary finishes and is requested once`,async({page},testInfo)=>{
    const calls=await fixture(page);
    await page.goto(`/stocks?code=005930&tab=${encodeURIComponent('요약')}&${query}`);
    await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
    await expect.poll(()=>calls.some(row=>row.path===path&&row.end)).toBe(true);
    const summary=calls.find(row=>row.path==='/securities/005930?view=summary')!;
    const chart=calls.find(row=>row.path===path)!;
    await testInfo.attach('first-chart-timing',{body:JSON.stringify({fixture:true,summaryDelayMs:600,chartDelayMs:250,chartStartAfterSummaryMs:chart.start-summary.start,chartResponseAfterSummaryStartMs:chart.end!-summary.start,summaryResponseMs:summary.end!-summary.start}),contentType:'application/json'});
    expect(chart.start).toBeLessThan(summary.end!);
    expect(calls.filter(row=>/\/(chart|bars|price-window)\?/.test(row.path)&&!row.path.includes('view=returns'))).toHaveLength(1);
    if(name==='line')await expect(page.locator('.mk-chart-session')).toContainText('2026.10.07 · 통합 · 이전 거래일');
  });
}

test('summary failure does not block the independent chart request',async({page})=>{
  const calls=await fixture(page,{summaryError:true});
  await page.goto('/stocks?code=005930');
  await expect(page.locator('.mk-loading')).toContainText('종목 정보를 불러오지 못했습니다.');
  expect(calls.filter(row=>row.path==='/securities/005930/chart?period=day')).toHaveLength(1);
});

test('chart failure leaves company summary usable without a duplicate retry',async({page})=>{
  const calls=await fixture(page,{chartError:true});await page.goto('/stocks?code=005930');
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expect(page.locator('.mk-chart-panel').getByRole('button',{name:'일봉 차트 보기'})).toBeVisible();
  expect(calls.filter(row=>row.path==='/securities/005930/chart?period=day')).toHaveLength(1);
});

test('hidden delayed summary discards an expired first chart and keeps source dates',async({page})=>{
  await page.clock.install();
  let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
  const calls=await fixture(page,{summaryGate:gate,summaryDelay:0});
  await page.goto('/stocks?code=005930');
  const path='/securities/005930/chart?period=day';
  await expect.poll(()=>calls.some(row=>row.path===path&&row.end)).toBe(true);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  await page.clock.setSystemTime(Date.now()+61000);release();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  expect(calls.filter(row=>row.path===path)).toHaveLength(1);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  await expect.poll(()=>calls.filter(row=>row.path===path&&row.end)).toHaveLength(2);
  await expect(page.locator('.mk-chart-session')).toContainText('2026.10.07 · 통합 · 이전 거래일');
  await page.clock.fastForward(60100);
  await expect.poll(()=>calls.filter(row=>row.path===path)).toHaveLength(3);
});

test('period change and leaving detail cancel outstanding chart requests',async({page})=>{
  await page.addInitScript(()=>{
    const original=window.fetch;
    const target=window as Window&{chartAborts?:string[]};target.chartAborts=[];
    window.fetch=(...args)=>{const path=String(args[0]);if(path.includes('/chart?'))args[1]?.signal?.addEventListener('abort',()=>target.chartAborts!.push(path),{once:true});return original(...args);};
  });
  const calls=await fixture(page,{summaryDelay:0,chartDelay:2500});
  await page.goto('/stocks?code=005930');
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'1주',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as Window&{chartAborts?:string[]}).chartAborts?.some(path=>path.endsWith('period=day')))).toBe(true);
  await expect.poll(()=>calls.filter(row=>row.path==='/securities/005930/chart?period=week')).toHaveLength(1);
  await page.getByRole('button',{name:'목록으로 돌아가기',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as Window&{chartAborts?:string[]}).chartAborts?.some(path=>path.endsWith('period=week')))).toBe(true);
});

test('retry replaces a failed history preload without repeating the summary',async({page})=>{
  const options={chartError:true};const calls=await fixture(page,options);
  await page.goto('/stocks?code=005930&mode=history&range=252');
  const panel=page.locator('.mk-chart-panel');
  await expect(panel.getByRole('button',{name:'다시 시도',exact:true})).toBeVisible();
  const summaries=calls.filter(row=>row.path==='/securities/005930?view=summary').length;
  options.chartError=false;await panel.getByRole('button',{name:'다시 시도',exact:true}).click();
  await expect(panel.locator('.mk-chart-caption')).toContainText('99');
  expect(calls.filter(row=>row.path==='/securities/005930/price-window?range=252&interval=day')).toHaveLength(2);
  expect(calls.filter(row=>row.path==='/securities/005930?view=summary')).toHaveLength(summaries);
});

test('saved chart preferences are resolved before requesting any default chart',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('stocks.chart.005930',JSON.stringify({mode:'history',range:1260,bar:'month',kind:'candle'})));
  const calls=await fixture(page);await page.goto('/stocks?code=005930');
  await expect(page.locator('.mk-chart-caption')).toContainText('99');
  expect(calls.filter(row=>row.path.includes('/chart?')||row.path.includes('/bars?'))).toHaveLength(0);
  expect(calls.filter(row=>row.path==='/securities/005930/price-window?range=1260&interval=month')).toHaveLength(1);
});
