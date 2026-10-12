import {test,expect} from '@playwright/test';
import {installAnalysisFixture} from './analysis-fixture';
test.beforeEach(async({page})=>{await installAnalysisFixture(page);});
test('cash conversion is requested on opening and displays server-validated numbers only',async({page})=>{
 let calls=0;
 await page.route('**/analysis/financial-questions?**',route=>{
   calls++;
   return route.fulfill({json:{code:'005930',basis:'CFS',frequency:'ttm',status:'partial',period_start:'2025-07-01',period_end:'2026-06-30',
     rows:[{metric:'net_income',name:'당기순이익',status:'ready',value:10000000000,unit:'원'},
       {metric:'operating_cash',name:'영업현금흐름',status:'ready',value:15000000000,unit:'원'},
       {metric:'capex',name:'유형·무형자산 취득 지출',status:'unavailable',value:null,unit:'원',reason_label:'확인된 입력이 없어요'}],
     comparisons:[{metric:'cash_conversion',name:'순이익 대비 영업현금흐름',status:'ready',value:150,unit:'%'}]}});
 });
 await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주'));
 const panel=page.getByRole('region',{name:'실적과 주가',exact:true});
 await expect(panel.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 expect(calls).toBe(0);
 await panel.locator('summary').filter({hasText:/^이익과 현금흐름$/}).click();
 const cash=page.getByLabel('이익과 현금흐름 비교',{exact:true});
 await expect(cash.getByText('150%',{exact:true})).toBeVisible();
 await expect(cash.getByText(/확인된 입력이 없어요/)).toBeVisible();
 expect(calls).toBe(1);
});
test('failed financial comparison leaves the price chart usable and supports retry',async({page})=>{
 await page.route('**/analysis/financial-questions?**',route=>route.fulfill({status:503,json:{detail:'fixture failure'}}));
 await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주'));
 const panel=page.getByRole('region',{name:'실적과 주가',exact:true});
 await expect(panel.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 await panel.locator('summary').filter({hasText:/^이익과 현금흐름$/}).click();
 await expect(panel.getByText('이익·현금흐름을 불러오지 못했어요.',{exact:false})).toBeVisible();
 await expect(panel.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 await expect(panel.getByRole('button',{name:'다시 시도',exact:true})).toBeVisible();
});
