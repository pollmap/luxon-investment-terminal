import {test,expect} from '@playwright/test';
import {installAnalysisFixture} from './analysis-fixture';
test.beforeEach(async({page})=>{await installAnalysisFixture(page);});

test('composition Enter does not open a stock; committed keyboard choice does',async({page})=>{
 await page.goto('/stocks');const input=page.getByRole('combobox',{name:'종목명 또는 코드 검색'});
 await input.focus();await input.dispatchEvent('compositionstart');await input.fill('삼성전자');await input.press('Enter');
 expect(new URL(page.url()).searchParams.has('code')).toBe(false);
 await input.dispatchEvent('compositionend');await expect(page.getByRole('listbox',{name:'검색 결과'}).getByRole('option')).toContainText('삼성전자');
 await input.press('ArrowDown');await input.press('Enter');await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
});
test('detail tabs support arrows and an associated panel',async({page})=>{
 await page.goto('/stocks?code=005930');const price=page.getByRole('tab',{name:'시세',exact:true});await price.focus();await price.press('ArrowRight');
 await expect(page.getByRole('tab',{name:'수급',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(page.getByRole('tabpanel',{name:'수급',exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'수급',exact:true}).press('Home');await expect(price).toHaveAttribute('aria-selected','true');
});
test('keyboard pane resizing keeps the same live chart canvas',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});await page.goto('/stocks?code=005930');const canvas=page.locator('.mk-chart-panel .mk-financial-canvas canvas').first();await expect(canvas).toBeVisible();
 const original=await canvas.elementHandle(),list=page.getByRole('separator',{name:/종목 목록 너비/});await expect(list).toHaveAttribute('aria-valuenow','240');await list.focus();await list.press('ArrowRight');await expect(list).toHaveAttribute('aria-valuenow','256');
 const depth=page.getByRole('separator',{name:/호가 너비/});await depth.focus();await depth.press('ArrowLeft');await expect(depth).toHaveAttribute('aria-valuenow','296');await depth.press('Escape');await expect(depth).toHaveAttribute('aria-valuenow','280');
 expect(await original!.evaluate(node=>node.isConnected)).toBe(true);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
test('failed company summary leaves independently loaded price data visible',async({page})=>{
 await page.route('**/securities/005930?view=summary',route=>route.fulfill({status:503,json:{detail:'fixture_non_production_summary_failure'}}));
 await page.goto('/stocks?code=005930');await expect(page.locator('.mk-detail-summary-status')).toContainText('종목 정보를 불러오지 못했습니다.');
 await expect(page.locator('.mk-chart-panel .mk-financial-canvas canvas').first()).toBeVisible();await expect(page.locator('.mk-chart-caption')).toContainText('205');
});
test('period and candle interval keep selected date and expose actual storage bounds',async({page})=>{
 await page.goto('/stocks?code=005930');const chart=page.locator('.mk-chart-panel');await expect(chart.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 await chart.getByRole('button',{name:'이전 날짜',exact:true}).click();const date=await chart.locator('.mk-chart-caption>strong').textContent(),canvas=await chart.locator('.mk-financial-canvas canvas').first().elementHandle();
 await chart.getByRole('button',{name:'1년',exact:true}).click();await chart.locator('.mk-price-controls-desktop').getByLabel('차트 봉 선택').selectOption('week');await expect(chart.locator('.mk-chart-caption>strong')).toHaveText(date!);expect(await canvas!.evaluate(node=>node.isConnected)).toBe(true);
 await chart.locator('.mk-price-coverage>summary').click();await expect(chart.locator('.mk-price-coverage')).toContainText('저장된 기간 2024-01-02 ~ 2026-10-08');await expect(chart.locator('.mk-price-coverage')).toContainText('이전 가격 자료가 더 남아 있어요.');
});
test('comparison controls appear only after selecting compare mode and close together',async({page})=>{
 await page.goto('/stocks');await expect(page.locator('.mk-stock-table input[type=checkbox]')).toHaveCount(0);await page.locator('.mk-rank-controls').getByRole('button',{name:'비교 선택',exact:true}).click();
 await page.locator('.mk-stock-table').getByLabel('삼성전자 비교 선택').check();await page.locator('.mk-stock-table').getByLabel('현대차 비교 선택').check();await expect(page.getByRole('region',{name:'선택한 종목 비교'})).toBeVisible();
 await page.locator('.mk-rank-controls').getByRole('button',{name:'비교 취소',exact:true}).click();await expect(page.locator('.mk-stock-table input[type=checkbox]')).toHaveCount(0);await expect(page.getByRole('region',{name:'선택한 종목 비교'})).toHaveCount(0);
});
test('favorite undo restores the actual saved selection',async({page})=>{
 await page.goto('/stocks');const table=page.locator('.mk-stock-table');await table.getByRole('button',{name:'삼성전자 관심종목 추가',exact:true}).click();await expect(table.getByRole('button',{name:'삼성전자 관심종목 해제',exact:true})).toBeVisible();
 await page.locator('.mk-toast').getByRole('button',{name:'취소',exact:true}).click();await expect(table.getByRole('button',{name:'삼성전자 관심종목 추가',exact:true})).toBeVisible();
});
test('zero-result recovery counts come from the screener and applying one restores rows',async({page})=>{
 await page.route('**/api/market/v1/screener',route=>{const query=route.request().postDataJSON();return route.fulfill({json:{fixture_non_production:true,rows:query.rules?.length?[]:[{code:'005930',name:'삼성전자',market:'KOSPI',price:100,change:1,as_of:'2026-10-08',facts:{}}],total:query.rules?.length?0:1}});});
 const filters={q:'',market:'ALL',rules:[{metric:'per',operator:'gte',value:999}],any_rules:[],sort:'value',direction:'desc'};
 await page.goto('/stocks?page=screener&filters='+encodeURIComponent(JSON.stringify(filters)));await page.getByRole('button',{name:'PER 조건 빼기 · 1개',exact:true}).click();await expect(page.locator('.mk-stock-link').first()).toContainText('삼성전자');
});
for(const width of [360,390,430,768,1024,1280,1920])test(`workspace retains usable chart at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:1000});await page.goto('/stocks?code=005930');await expect(page.locator('.mk-chart-panel .mk-financial-canvas canvas').first()).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
test('reduced motion disables the interaction durations',async({page})=>{
 await page.emulateMedia({reducedMotion:'reduce'});await page.goto('/stocks');expect(await page.locator('.mk-app').evaluate(element=>getComputedStyle(element).getPropertyValue('--mk-motion-tab').trim())).toBe('0ms');
});
