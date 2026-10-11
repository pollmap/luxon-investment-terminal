import {test,expect} from '@playwright/test';
for(const width of [360,390,430]){
 test(`phone reading and explicit selection at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844});
  await page.goto('/stocks');
  await expect(page.getByRole('heading',{name:'오늘의 시장'})).toBeVisible();
  const search=page.getByRole('combobox',{name:'종목명 또는 코드 검색'});
  await expect(search).toBeVisible();
  const header=await page.locator('.mk-header').boundingBox();
  const heading=await page.getByRole('heading',{name:'오늘의 시장'}).boundingBox();
  expect(header!.y+header!.height).toBeLessThanOrEqual(heading!.y);
  expect((await page.locator('.mk-header-search').boundingBox())!.width).toBeGreaterThan(width-60);
  await expect(page.locator('.mk-mobile-list input[type=checkbox]')).toHaveCount(0);
  await page.getByRole('button',{name:'비교 선택',exact:true}).click();
  await expect(page.locator('.mk-mobile-list input[type=checkbox]').first()).toBeVisible();
  await page.getByRole('button',{name:'비교 취소',exact:true}).click();
  await page.getByRole('button',{name:'글자 크기',exact:true}).click();
  await page.getByRole('button',{name:'더 크게',exact:true}).click();
  await page.getByRole('button',{name:'이 크기로 보기'}).click();
  await expect(page.locator('.mk-app')).toHaveClass(/mk-largest/);
  await page.reload();
  await expect(page.locator('.mk-app')).toHaveClass(/mk-largest/);
  await page.getByRole('button',{name:'순위 변경',exact:true}).click();
  await page.getByRole('button',{name:'많이 내린 종목'}).click();
  await expect(page.getByRole('heading',{name:/하락률 상위/})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 });
}
test('analysis calculator rejects invalid input and shows explicit assumptions',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.goto('/stocks?code=005930');
 await page.getByRole('tab',{name:'기업정보',exact:true}).click();
 await page.getByRole('button',{name:'실적·재무·가치평가',exact:true}).click();
 await page.getByText('주가·실적 비교와 계산기',{exact:true}).click();
 await expect(page.getByRole('heading',{name:'주가와 실적 함께 보기'})).toBeVisible();
 await page.getByText('내 가정으로 계산하기',{exact:true}).click();
 await page.getByLabel('계산 시작 지표').fill('0');
 await page.getByRole('button',{name:'내 가정으로 계산',exact:true}).click();
 expect(await page.getByLabel('계산 시작 지표').evaluate(el=>(el as HTMLInputElement).validity.valid)).toBe(false);
 await page.getByLabel('계산 시작 지표').fill('100');
 await page.getByLabel('가정 성장률').fill('10');
 await page.getByRole('button',{name:'내 가정으로 계산',exact:true}).click();
 await expect(page.locator('.mk-analysis')).toContainText('1,997');
 await page.getByLabel('가정 성장률').fill('0');
 await expect(page.getByText('사용자 가정에 따른 계산 · 배당 제외',{exact:true})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('rapid favorite touches save once and a replacement dialog respects back',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.goto('/stocks');
 const star=page.locator('.mk-mobile-stock .mk-star').first();
 await star.dblclick();
 await expect(star).toHaveAttribute('aria-pressed','true');
 await page.getByRole('button',{name:'화면 설정',exact:true}).click();
 await page.getByRole('dialog').getByRole('button',{name:/^글자 크기/}).click();
 await expect(page.getByRole('dialog',{name:'글자 크기',exact:true})).toBeVisible();
 await page.goBack();
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'오늘의 시장'})).toBeVisible();
});
