import {test, expect} from '@playwright/test';

test('phone financing separates balances and dates, supports touch, and retries errors', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  let fail = false;
  await page.route('**/api/market/v1/securities/005930/financing?*', route => {
    if (fail) return route.fulfill({status:503,body:'fixture_non_production'});
    return route.fulfill({json:{
      lending:{status:'ready',rows:[{date:'2026-09-22',balance:100,new:0,repaid:null,ratio:null},{date:'2026-09-23',balance:110,new:20,repaid:10,ratio:null}]},
      credit:{status:'ready',rows:[{date:'2026-09-23',trade_date:'2026-09-21',balance:200,new:0,repaid:3,ratio:1.5}]},
      stock_loan:{status:'credentials_missing',rows:[]},
    }});
  });
  await page.goto('/stocks?code=005930');
  await page.getByRole('tab',{name:'수급',exact:true}).click();
  await page.getByRole('button',{name:'대차·신용',exact:true}).click();
  const panel=page.locator('.mk-financing');
  await expect(panel.locator('.mk-flow-totals')).toContainText('110주');
  await page.getByRole('slider',{name:'대차잔고 잔고수량 날짜'}).focus();
  await page.keyboard.press('Home');
  await expect(panel.locator('.mk-trend-reading')).toContainText('2026-09-22');
  await expect(panel.locator('.mk-trend-reading')).toContainText('100주');
  await page.getByRole('button',{name:'신용융자',exact:true}).click();
  await expect(panel).toContainText('2026-09-23 · 결제일 기준 (매매일 2026-09-21)');
  await expect(panel.locator('.mk-flow-totals')).toContainText('200주');
  await page.getByRole('button',{name:'대차·신용 설명',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('공매도 순보유잔고와 구분');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'대차·신용 설명',exact:true})).toBeFocused();
  await page.getByRole('button',{name:'신용대주',exact:true}).click();
  await expect(panel).toContainText('자료 연결을 준비하고 있습니다.');
  fail=true;
  await panel.getByRole('button',{name:'1년',exact:true}).click();
  await expect(panel).toContainText('자료를 불러오지 못했습니다.');
  fail=false;
  await panel.getByRole('button',{name:'다시 불러오기'}).click();
  await page.getByRole('button',{name:'신용융자',exact:true}).click();
  await expect(panel.locator('.mk-flow-totals')).toContainText('200주');
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'../../docs/screenshots/mobile-financing-contract.png',fullPage:true});
  await page.getByRole('button',{name:'화면 설정',exact:true}).click();
  await page.getByRole('button',{name:'다크',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:/^글자 크기/}).click();
  await page.getByRole('button',{name:'더 크게',exact:true}).click();
  await page.getByRole('button',{name:'이 크기로 보기',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'../../docs/screenshots/mobile-financing-contract-dark.png',fullPage:true});
});

test('real financing endpoint returns an explicit state without fixtures or account access',async ({page})=>{
  await page.setViewportSize({width:360,height:800});
  const response=await page.request.get('/api/market/v1/securities/005930/financing');
  expect(response.ok()).toBeTruthy();
  const data=await response.json();
  for(const key of ['lending','credit','stock_loan']) {
    expect(['ready','partial','stale','pending','no_data','unavailable','credentials_missing','error']).toContain(data[key].status);
    expect(Array.isArray(data[key].rows)).toBeTruthy();
  }
  await page.goto('/stocks?code=005930');
  await page.getByRole('tab',{name:'수급',exact:true}).click();
  await page.getByRole('button',{name:'대차·신용',exact:true}).click();
  await expect(page.locator('.mk-financing')).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:'../../docs/screenshots/mobile-financing-live.png',fullPage:true});
});
