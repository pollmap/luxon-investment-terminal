import {test, expect, type Page} from '@playwright/test';

// These invented prices exercise navigation only, never market-data validation.
async function fixture(page:Page) {
  await page.addInitScript(() => Object.defineProperty(navigator, 'onLine', {configurable:true, get:()=>true}));
  await page.routeWebSocket('**/api/market/v1/stream', socket => socket.close());
  const stocks = Array.from({length:12}, (_,i) => ({
    code:i === 0?'005930':i === 1?'000660':String(100000+i),
    name:i === 0?'삼성전자':i === 1?'SK하이닉스':`탐색 시험 종목 ${i}`,
    market:'KOSPI',price:100+i,change:1,volume:10,value:1000-i,cap:10000-i,
    as_of:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',facts:{},fixture_non_production:true,
  }));
  await page.route('**/api/market/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace('/api/market/v1','');
    const stock = stocks.find(row => path.includes(row.code)) || stocks[0];
    let data:unknown = {rows:[],status:'ready',fixture_non_production:true};
    if (path === '/metrics') data = {};
    else if (path === '/search-index') data = {rows:stocks.map(row => ({...row,tokens:[row.code,row.name]}))};
    else if (path === '/screener') data = {rows:stocks,total:stocks.length};
    else if (path === '/overview') data = {indices:[],breadth:{up:12,down:0,flat:0},total:12,value:1000,sectors:[],filings:{rows:[],status:'ready'}};
    else if (/^\/securities\/[0-9A-Z]{6}$/.test(path)) data = {security:stock,detail:{financials:[],flows:[]},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
    else if (path.endsWith('/quote')) data = stock;
    else if (path.endsWith('/chart')) data = {status:'ready',points:[{time:'20261008090000',value:100},{time:'20261008150000',value:101}]};
    await route.fulfill({json:data,headers:{Date:new Date().toUTCString(),'Cache-Control':'no-store'}});
  });
}

async function expectRoute(page:Page, expected:Record<string,string|null>) {
  // Wait for the async Next router restore and subsequent React commit as well
  // as the synchronous History API write (the original regression was delayed).
  await expect.poll(() => page.evaluate(keys => Object.fromEntries(keys.map(key => [key,new URLSearchParams(location.search).get(key)])),Object.keys(expected))).toEqual(expected);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect.poll(() => page.evaluate(keys => Object.fromEntries(keys.map(key => [key,new URLSearchParams(location.search).get(key)])),Object.keys(expected))).toEqual(expected);
}

async function expectListRoute(page:Page, listURL:string) {
  await expectRoute(page,{code:null});
  const before = JSON.parse(new URL(listURL).searchParams.get('filters') || '{}');
  await expect.poll(() => page.evaluate(() => JSON.parse(new URLSearchParams(location.search).get('filters') || '{}'))).toMatchObject(before);
}

for (const width of [1440,390]) {
  test(`search Enter preserves detail URL, chart selection, reload and history at ${width}px`, async({page}) => {
    await page.setViewportSize({width,height:900});await fixture(page);
    await page.goto('/stocks');
    await expect(page.locator('.mk-stock-link:visible,.mk-mobile-stock-open:visible').first()).toBeVisible();
    if(width<768){await page.getByRole('button',{name:'순위 변경',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'시가총액 큰 종목',exact:true}).click();}
    else await page.getByRole('button',{name:'시가총액',exact:true}).click();
    const listURL = page.url();
    await page.evaluate(() => window.scrollTo(0,300));
    const scroll = await page.evaluate(() => scrollY);
    const search = page.getByRole('combobox',{name:'종목명 또는 코드 검색'});
    await search.fill('005930');
    await expect(page.getByRole('option').first()).toContainText('삼성전자');
    await search.press('Enter');
    await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
    await expectRoute(page,{code:'005930'});
    if(width<768) await page.getByRole('combobox',{name:'차트 기간 선택',exact:true}).selectOption('intraday');
    else await page.getByRole('button',{name:'하루',exact:true}).click();
    await page.getByRole('button',{name:'캔들 · 5분',exact:true}).click();
    await expectRoute(page,{code:'005930',kind:'candle',period:'day'});
    await page.getByRole('button',{name:'관심종목에 추가',exact:true}).click();
    await page.goBack();
    await expectListRoute(page,listURL);
    await expect(page.getByRole('heading',{name:/시가총액 상위/})).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(scroll);
    await page.goForward();
    await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
    await expectRoute(page,{code:'005930',kind:'candle',period:'day'});
    await expect(page.getByRole('button',{name:'캔들 · 5분',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.getByRole('button',{name:'1주',exact:true}).click();
    await expectRoute(page,{code:'005930',kind:'line',period:'week'});
    await page.reload();
    await expect(page.getByRole('button',{name:'1주',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(page.getByRole('button',{name:'관심종목 저장됨',exact:true})).toBeVisible();
    await expectRoute(page,{code:'005930',kind:'line',period:'week'});
    await page.getByRole('button',{name:'목록으로 돌아가기',exact:true}).click();
    await expectListRoute(page,listURL);
  });
}

test('direct shared chart options override stored choices and dialogs preserve the route', async({page}) => {
  await fixture(page);
  await page.addInitScript(() => localStorage.setItem('stocks.chart.005930',JSON.stringify({kind:'line',period:'week'})));
  await page.goto('/stocks?code=005930&tab='+encodeURIComponent('요약')+'&kind=candle&period=day');
  await expect(page.getByRole('button',{name:'캔들 · 5분',exact:true})).toHaveAttribute('aria-pressed','true');
  const detailURL = page.url();
  await page.getByRole('button',{name:'화면 설정',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button',{name:'다크',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page).toHaveURL(detailURL);
  await page.keyboard.press('Control+k');
  await page.getByRole('dialog').getByRole('textbox',{name:'종목명 또는 코드'}).fill('000660');
  await page.getByRole('dialog').locator('.mk-search-result').first().click();
  await expect(page.getByRole('heading',{name:'SK하이닉스',exact:true})).toBeVisible();
  await expectRoute(page,{code:'000660',kind:null,period:null});
  await page.goBack();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expectRoute(page,{code:'005930',kind:'candle',period:'day'});
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('PC stock row opens full workspace without a preview dialog', async({page}) => {
  await fixture(page);await page.goto('/stocks');
  await expect(page.locator('.mk-stock-link[data-code="005930"]')).toBeVisible();
  await expect(page.getByRole('button',{name:'삼성전자 빠른 확인',exact:true})).toHaveCount(0);
  await page.locator('.mk-stock-table tbody tr').first().locator('td').nth(1).click();
  await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
  await expectRoute(page,{code:'005930'});await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button',{name:'목록으로 돌아가기',exact:true}).click();
  await expectRoute(page,{code:null});
  await expect(page.locator('.mk-stock-link[data-code="005930"]')).toBeFocused();
});
