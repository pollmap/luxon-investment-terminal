import {test, expect} from '@playwright/test';

test('mobile investor flows use touch dates, preserve missing values, and sort brokers', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.route(/\/api\/market\/v1\/securities\/005930(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const data = await response.json();
    // Contract fixture, never written to the application database.
    data.detail.flows = [
      {date:'2026-09-23',individual:null,institution:-100,foreign:300,holding:40},
      {date:'2026-09-22',individual:200,institution:0,foreign:-100,holding:39.9},
    ];
    data.detail.brokers = [
      {name:'fixture 증권 A',buy:100,sell:null,net:null},
      {name:'fixture 증권 B',buy:300,sell:100,net:200},
    ];
    await route.fulfill({response,json:data});
  });
  await page.goto('/stocks?code=005930');
  await page.getByRole('tab',{name:'수급',exact:true}).click();
  await expect(page.locator('.mk-flow-totals button').filter({hasText:'개인'}).locator('strong')).toHaveText('—');
  await expect(page.locator('.mk-flow-totals button').filter({hasText:'외국인'}).locator('strong')).toHaveText('+200');
  await page.getByRole('slider',{name:'외국인 순매수 날짜'}).focus();
  await page.keyboard.press('Home');
  await expect(page.locator('.mk-trend-reading')).toContainText('2026-09-22');
  await expect(page.locator('.mk-trend-reading')).toContainText('-100주');
  await page.getByRole('button',{name:'수급과 거래원 설명'}).click();
  await expect(page.getByRole('dialog')).toContainText('외국인 보유율');
  await page.goBack();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button',{name:'증권사별',exact:true}).click();
  await expect(page.locator('.mk-broker-ranks li').first()).toContainText('fixture 증권 B');
  await page.getByRole('button',{name:'순매수',exact:true}).click();
  await expect(page.locator('.mk-broker-ranks li').filter({hasText:'fixture 증권 A'}).locator('strong')).toContainText('—');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('short trades and balances have separate dates; retry restores real-shaped data', async ({page}) => {
  await page.setViewportSize({width:360,height:800});
  let fail = true;
  await page.route('**/api/market/v1/securities/005930/short-selling?*', route => {
    if (fail) return route.fulfill({status:503,json:{detail:'fixture_non_production'}});
    return route.fulfill({json:{trades:{status:'ready',rows:[
      {date:'2026-09-22',volume:1000,value:100000000,ratio:2},
      {date:'2026-09-23',volume:0,value:0,ratio:0},
    ]},balance:{status:'ready',rows:[{date:'2026-09-21',volume:300000,value:60000000000,ratio:0.4}]}}});
  });
  await page.goto('/stocks?code=005930');
  await page.getByRole('tab',{name:'공매도',exact:true}).click();
  await expect(page.getByText('자료를 불러오지 못했습니다.',{exact:true})).toBeVisible();
  fail = false;
  await page.getByRole('button',{name:'다시 불러오기',exact:true}).click();
  await expect(page.locator('.mk-short-selling')).toContainText('2026-09-23 기준');
  await expect(page.locator('.mk-short-selling .mk-flow-totals strong').first()).toHaveText('0주');
  await page.getByRole('button',{name:'잔고',exact:true}).click();
  await expect(page.getByRole('heading',{name:'공매도 순보유잔고',exact:true})).toBeVisible();
  await expect(page.locator('.mk-short-selling')).toContainText('2026-09-21 기준');
  await expect(page.locator('.mk-short-selling .mk-flow-totals strong').first()).toHaveText('300,000주');
  await page.getByRole('button',{name:'공매도 거래와 잔고 설명'}).click();
  await expect(page.getByRole('dialog')).toContainText('거래량의 누적 합계가 아닙니다');
  await page.goBack();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('mobile activity works with actual stored observations and large dark text', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/stocks?code=005930');
  await page.getByRole('tab',{name:'수급',exact:true}).click();
  await expect(page.locator('.mk-flow-totals')).toBeVisible();
  await page.screenshot({path:'../../docs/screenshots/mobile-flow-v3.png',fullPage:true});
  await page.getByRole('button',{name:'증권사별',exact:true}).click();
  await expect(page.locator('.mk-broker-ranks li').first()).toBeVisible();
  await page.screenshot({path:'../../docs/screenshots/mobile-brokers-v3.png',fullPage:true});
  await page.getByRole('button',{name:'화면 설정',exact:true}).click();
  await page.getByRole('button',{name:'다크',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:/^글자 크기/}).click();
  await page.getByRole('button',{name:'더 크게',exact:true}).click();
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  await expect(page.locator('.mk-broker-ranks li').first()).toBeVisible();
  await page.screenshot({path:'../../docs/screenshots/mobile-brokers-v3-dark.png',fullPage:true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
