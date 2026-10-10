import {test,expect,type Page} from '@playwright/test';

// Invented values isolate layout/navigation from provider availability.
async function fixture(page:Page, slowActivity=false) {
  const calls:string[]=[];
  const stocks=Array.from({length:12},(_,i)=>({code:i===0?'005930':i===1?'000660':String(100000+i),name:i===0?'삼성전자':i===1?'SK하이닉스':`시험 종목 ${i}`,market:'KOSPI',price:101000+i*100,change:1.25,volume:123000,value:123000000,cap:123000000000,as_of:'2026-10-08T15:30:00+09:00',received_at:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',facts:{},fixture_non_production:true}));
  const bars=Array.from({length:65},(_,i)=>({date:new Date(Date.UTC(2026,7,1+i)).toISOString().slice(0,10),open:99000+i*25,high:101000+i*25,low:98000+i*25,close:100000+i*25,volume:100000+i*100,ma20:i>19?99000+i*25:null}));
  await page.routeWebSocket('**/api/market/v1/stream',()=>{});
  await page.route('**/api/market/v1/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace('/api/market/v1','');calls.push(path+url.search);
    const stock=stocks.find(row=>path.includes(row.code))??stocks[0];
    let data:unknown={rows:[],status:'ready',fixture_non_production:true};
    if(path==='/metrics')data={};
    else if(path==='/search-index')data={rows:stocks.map(row=>({...row,tokens:[row.code,row.name]}))};
    else if(path==='/screener')data={rows:stocks,total:stocks.length};
    else if(path==='/overview')data={indices:[],breadth:{up:12,down:0,flat:0},total:12,value:1000,sectors:[],filings:{rows:[],status:'ready'}};
    else if(/^\/securities\/[0-9A-Z]{6}$/.test(path)){
      if(slowActivity&&url.searchParams.get('view')==='activity')await new Promise(resolve=>setTimeout(resolve,1800));
      data={security:stock,detail:{facts:{},financials:[],flows:[]},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
    }else if(path.endsWith('/quote'))data=stock;
    else if(path.endsWith('/price-window'))data={rows:bars,status:'ready'};
    else if(path.endsWith('/chart'))data={status:'ready',points:[{time:'20261008090000',value:100000},{time:'20261008150000',value:101000}]};
    else if(path.endsWith('/depth'))data={book_status:'ready',trades_status:'ready',venue:'KRX',book:Array.from({length:10},(_,i)=>({level:i+1,ask:101000+i*100,bid:100900-i*100,ask_quantity:1000+i*10,bid_quantity:800+i*10})),trades:[{time:'15:30:00',price:101000,quantity:100}]};
    await route.fulfill({json:data});
  });
  return calls;
}

for(const width of [360,390,430,768,1024,1100,1280,1440,1920])test(`one chart and adaptive depth workspace at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:900});const calls=await fixture(page);
  await page.goto('/stocks?code=005930');
  const canvas=page.locator('.mk-chart-panel .mk-financial-canvas');
  await expect(canvas).toHaveCount(1);await expect(canvas.locator('canvas').first()).toBeVisible();
  if(width<768){
    await expect(page.getByRole('combobox',{name:'차트 기간 선택',exact:true})).toHaveValue('63');
    await expect(page.getByRole('combobox',{name:'가격 차트 모양',exact:true})).toHaveValue('candle');
  }else{
    await expect(page.getByRole('button',{name:'3개월',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(page.getByRole('button',{name:'캔들',exact:true})).toHaveAttribute('aria-pressed','true');
  }
  await expect(page.getByRole('combobox',{name:'차트 봉 선택'})).toHaveValue('day');
  expect(calls.filter(path=>path.includes('/price-window?range='))).toEqual(['/securities/005930/price-window?range=63&interval=day']);
  expect(calls.some(path=>path.includes('/chart?')||path.includes('/bars?'))).toBe(false);
  expect(await canvas.evaluate(el=>Math.round(el.getBoundingClientRect().height))).toBe(width<768?320:420);
  await expect(page.locator('.mk-workspace-list')).toBeVisible({visible:width>=1024});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  if(width>=1280){
    await expect(page.locator('.mk-workspace-depth')).toBeVisible();
    await expect(page.getByRole('button',{name:'호가·체결 보기',exact:true})).toHaveCount(0);
    expect(await page.locator('.mk-workspace-list').evaluate(el=>Math.round(el.getBoundingClientRect().width))).toBe(240);
    expect(await page.locator('.mk-workspace-depth').evaluate(el=>Math.round(el.getBoundingClientRect().width))).toBe(280);
  }else{
    await expect(page.locator('.mk-depth-panel')).toHaveCount(0);
    await page.getByRole('button',{name:'호가·체결 보기',exact:true}).click();
    await expect(page.getByRole('dialog')).toBeVisible();await expect(page.locator('.mk-depth-panel')).toHaveCount(1);
    await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);await expect(canvas).toHaveCount(1);
  }
  await expect(page.getByRole('heading',{name:'실적의 흐름',exact:true})).toHaveCount(0);
  await page.screenshot({path:info.outputPath(`workspace-${width}-fixture.png`)});
});

test('responsive depth relocation retains same-symbol values during a delayed refresh',async({page})=>{
  await fixture(page);await page.setViewportSize({width:1440,height:900});
  await page.goto('/stocks?code=005930');
  await expect(page.locator('.mk-depth-panel table')).toBeVisible();
  let release:()=>void=()=>{};
  const pending=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/securities/005930/depth',async route=>{await pending;await route.fallback();});
  try {
    await page.setViewportSize({width:768,height:900});
    await page.getByRole('button',{name:'호가·체결 보기',exact:true}).click();
    await expect(page.getByRole('dialog').locator('.mk-depth-panel table')).toBeVisible();
    await expect(page.getByRole('dialog').getByText('연결 확인 중',{exact:true})).toBeVisible();
    await page.setViewportSize({width:1440,height:900});
    await expect(page.locator('.mk-workspace-depth .mk-depth-panel table')).toBeVisible();
    await expect(page.locator('.mk-depth-panel')).toHaveCount(1);
  } finally { release(); }
});

test('shared selection overrides saved settings; reset and reload retain chart defaults',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('stocks.chart.005930',JSON.stringify({mode:'intraday',kind:'line',period:'week'})));
  const calls=await fixture(page);await page.goto('/stocks?code=005930&mode=history&range=1260&kind=bar&bar=month');
  await expect(page.locator('.mk-chart-caption')).toBeVisible();
  expect(calls.filter(path=>path.includes('/price-window?range='))).toEqual(['/securities/005930/price-window?range=1260&interval=month']);
  await page.getByRole('button',{name:'기본 차트로',exact:true}).click();
  await expect(page.getByRole('button',{name:'3개월',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('button',{name:'캔들',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('combobox',{name:'차트 봉 선택'})).toHaveValue('day');
  await page.reload();
  await expect(page.getByRole('button',{name:'3개월',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('button',{name:'캔들',exact:true})).toHaveAttribute('aria-pressed','true');
});

test('row opens workspace, company switches preserve list state, tabs stay during delayed section loads',async({page})=>{
  await fixture(page,true);await page.goto('/stocks');
  await expect(page.locator('.mk-stock-table tbody tr')).toHaveCount(12);
  await expect(page.getByRole('button',{name:/빠른 확인/})).toHaveCount(0);
  await expect(page.locator('.mk-stock-table input[type=checkbox]')).toHaveCount(0);
  await page.getByRole('button',{name:'시가총액',exact:true}).click();
  await page.locator('.mk-stock-table tbody tr').first().locator('td').nth(1).click();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expect(page.locator('.mk-chart-panel .mk-chart-caption')).toBeVisible();
  await page.evaluate(()=>window.scrollTo(0,0));
  const headerY=await page.locator('.mk-security-header').evaluate(el=>el.getBoundingClientRect().top+scrollY);
  await page.getByRole('tab',{name:'수급',exact:true}).click();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expect(page.getByRole('tab',{name:'수급',exact:true})).toHaveAttribute('aria-selected','true');
  expect(await page.locator('.mk-security-header').evaluate(el=>el.getBoundingClientRect().top+scrollY)).toBe(headerY);
  await page.locator('.mk-workspace-list').getByRole('button',{name:/SK하이닉스 KOSPI/}).click();
  await expect(page.getByRole('heading',{name:'SK하이닉스',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'목록으로 돌아가기',exact:true}).click();
  await expect(page.getByRole('heading',{name:/시가총액 상위/})).toBeVisible();
  expect(new URLSearchParams(new URL(page.url()).search).get('code')).toBeNull();
});


test('resize moves depth once, retains chart choice and supports large dark text',async({page},info)=>{
  await fixture(page);await page.setViewportSize({width:1440,height:1000});await page.goto('/stocks?code=005930');
  await expect(page.locator('.mk-chart-caption')).toBeVisible();
  await page.getByRole('button',{name:'영역',exact:true}).click();
  await expect(page.locator('.mk-depth-panel')).toHaveCount(1);
  await page.setViewportSize({width:768,height:1000});
  await expect(page.locator('.mk-depth-panel')).toHaveCount(0);
  await page.getByRole('button',{name:'호가·체결 보기',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.setViewportSize({width:1440,height:1000});
  await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('.mk-depth-panel')).toHaveCount(1);
  await expect(page.getByRole('button',{name:'영역',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.mk-chart-panel .mk-financial-canvas')).toHaveCount(1);
  await page.getByRole('button',{name:'글자 크기',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'더 크게',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  await page.getByRole('button',{name:'화면 설정',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'다크',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  for(const width of [1440,768,390]){
    await page.setViewportSize({width,height:1000});
    if(width<768)await expect(page.getByRole('combobox',{name:'가격 차트 모양',exact:true})).toHaveValue('area');
    else await expect(page.getByRole('button',{name:'영역',exact:true})).toHaveAttribute('aria-pressed','true');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await page.screenshot({path:info.outputPath(`workspace-${width}-large-dark-fixture.png`)});
  }
});


test('mobile compact controls expose candles above the bottom navigation and preserve keyboard/URL choices',async({page},info)=>{
  await page.setViewportSize({width:390,height:844});await fixture(page);await page.goto('/stocks?code=005930');
  const canvas=page.locator('.mk-chart-panel .mk-financial-canvas');
  await expect(canvas.locator('canvas').first()).toBeVisible();
  const bounds=await canvas.boundingBox();
  const bottom=await page.locator('.mk-bottom-nav').evaluate(el=>el.getBoundingClientRect().top);
  expect(bounds!.y).toBeLessThan(600);expect(bottom-bounds!.y).toBeGreaterThanOrEqual(180);
  await info.attach('mobile-chart-position',{body:JSON.stringify({fixture:true,viewport:[390,844],chartTop:bounds!.y,visibleChartBeforeBottomNavigation:bottom-bounds!.y}),contentType:'application/json'});
  await page.screenshot({path:info.outputPath('mobile-390-compact-controls-fixture.png')});
  const period=page.getByRole('combobox',{name:'차트 기간 선택',exact:true});
  const bar=page.getByRole('combobox',{name:'차트 봉 선택',exact:true});
  const shape=page.getByRole('combobox',{name:'가격 차트 모양',exact:true});
  await period.selectOption('1260');await bar.selectOption('week');
  await shape.focus();await shape.press('Home');await shape.press('ArrowDown');
  await expect(shape).toHaveValue('area');
  await expect.poll(()=>new URL(page.url()).searchParams.get('kind')).toBe('area');
  await page.reload();await expect(period).toHaveValue('1260');await expect(bar).toHaveValue('week');await expect(shape).toHaveValue('area');
  await page.getByRole('button',{name:'기본 차트로',exact:true}).click();
  await expect(period).toHaveValue('63');await expect(bar).toHaveValue('day');await expect(shape).toHaveValue('candle');
  await expect(canvas).toHaveCount(1);
  await page.getByRole('button',{name:'글자 크기',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'더 크게',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await expect(period).toHaveValue('63');await expect(shape).toHaveValue('candle');
  await page.screenshot({path:info.outputPath('mobile-390-compact-large-fixture.png')});
});
