import {test,expect} from '@playwright/test';

for (const width of [360,390,430]) {
  test(`chart choices and depth remain usable on ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:844});
    await page.route('**/api/market/v1/securities/005930/depth',route=>route.fulfill({json:{
      venue:'KRX',book_status:'ready',trades_status:'ready',book:[{level:1,ask:100,bid:99,ask_quantity:null,bid_quantity:0}],
      trades:[{time:'10:00:00',price:100,quantity:12}]
    }}));
    await page.goto('/stocks?code=005930');
    await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'1주',exact:true}).click();
    await expect(page).toHaveURL(/period=week/);
    await page.reload();
    await expect(page.getByRole('button',{name:'1주',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.getByText('호가·체결 보기',{exact:true}).click();
    const panel=page.getByRole('region',{name:'호가·체결',exact:true});
    await expect(panel.locator('tbody td').first()).toHaveText('—');
    await expect(panel.locator('tbody td').last()).toHaveText('0');
    await panel.getByRole('tab',{name:'최근 체결',exact:true}).click();
    await expect(panel).toContainText('10:00:00');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  });
}

test('favorite undo and preferred start do not require login',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/stocks?code=005930');
  await page.getByRole('button',{name:'관심종목에 추가',exact:true}).click();
  await expect(page.getByRole('button',{name:'관심종목 저장됨',exact:true})).toBeVisible();
  await page.locator('.mk-toast').getByRole('button',{name:'취소',exact:true}).click();
  await expect(page.getByRole('button',{name:'관심종목에 추가',exact:true})).toBeVisible();
  await page.getByRole('navigation',{name:'모바일 주 메뉴'}).getByRole('button',{name:'더보기',exact:true}).click();
  await page.getByRole('button',{name:'화면·시작 설정'}).click();
  await page.getByLabel('처음 열리는 화면').selectOption('watchlist');
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('luxon.public-market.v1')||'{}').startPage)).toBe('watchlist');
  await page.goto('/stocks');
  await expect(page).toHaveURL(/page=watchlist/);
});
