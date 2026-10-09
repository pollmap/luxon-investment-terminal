import {test,expect} from '@playwright/test';

const catalogue={status:'ready',rows:[
 {id:'technology',name:'기술',parent_id:null,depth:0},
 {id:'rare',name:'희귀 소재',parent_id:'technology',depth:1},
 {id:'unranked',name:'원자재 분석',parent_id:'rare',depth:2},
 ...Array.from({length:297},(_,i)=>({id:`extra-${i}`,name:`추가 산업 ${i}`,parent_id:null,depth:0})),
]};
for(const width of [360,1440])test(`all 300 industries include unranked leaves and preserve hierarchy at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900});
 // Existing v1 state did not include view/parentId. Preserve its rank and period.
 await page.addInitScript(()=>sessionStorage.setItem('korea-stocks:industries:v1',JSON.stringify({kind:'TOP_GAINERS',duration:'1y',filter:'',limit:10})));
 let failCatalogue=true;
 await page.route('**/api/market/v1/sectors/rankings?*',route=>route.fulfill({json:{status:'ready',rows:[{id:'technology',name:'기술',rank:1,value:100000000,change:1}]}}));
 await page.route('**/api/market/v1/sectors/catalogue',route=>route.fulfill(failCatalogue?{status:503,json:{detail:'fixture failure'}}:{json:catalogue}));
 await page.route(/\/api\/market\/v1\/sectors\/(technology|rare|unranked)$/,route=>route.fulfill({json:{status:'ready',summary:'검증용 산업 설명',performance:[]}}));
 await page.route(/\/api\/market\/v1\/sectors\/(technology|rare|unranked)\/stocks$/,route=>route.fulfill({json:{status:'ready',rows:[{code:'005930',name:'삼성전자',market:'KOSPI',cap:null,value:null}]}}));
 await page.goto('/stocks');
 await page.locator('.mk-explorer').getByRole('tab',{name:'산업',exact:true}).click();
 const industry=page.getByRole('region',{name:'토스 산업 분류'});
 await expect(industry.getByRole('combobox',{name:'산업 기간'})).toHaveValue('1y');
 await industry.getByRole('textbox',{name:'산업 검색'}).fill('원자재');
 await expect(industry.getByRole('button',{name:'전체 산업',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(industry.getByRole('status')).toContainText('불러오지 못했어요');
 failCatalogue=false;
 await industry.getByRole('button',{name:'다시 시도',exact:true}).click();
 await expect(industry.locator('.mk-industry-catalogue')).toContainText('원자재 분석');
 await expect(industry.locator('.mk-industry-catalogue')).toContainText('기술 › 희귀 소재');
 await expect(industry.locator('.mk-industry-catalogue')).not.toContainText('거래대금');
 await expect(industry.getByRole('combobox',{name:'산업 기간'})).toHaveCount(0);
 await expect(industry.getByRole('button',{name:'전체 산업 300개',exact:true})).toBeVisible();
 await industry.getByRole('textbox',{name:'산업 검색'}).fill('');
 await industry.getByRole('button',{name:'기술 하위 산업 1개 보기',exact:true}).click();
 await industry.getByRole('button',{name:'희귀 소재 하위 산업 1개 보기',exact:true}).click();
 await expect(industry.getByRole('navigation',{name:'산업 분류 경로'})).toContainText('희귀 소재');
 const item=industry.locator('.mk-catalogue-open').filter({hasText:'원자재 분석'});
 await item.click();
 const dialog=page.getByRole('dialog',{name:'원자재 분석',exact:true});
 await expect(dialog.getByRole('navigation',{name:'선택한 산업 분류 경로'})).toContainText('기술');
 await dialog.getByRole('navigation',{name:'선택한 산업 분류 경로'}).getByRole('button',{name:'희귀 소재',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'희귀 소재',exact:true})).toBeVisible();
 await page.getByRole('dialog').getByRole('button',{name:'원자재 분석',exact:true}).click();
 await dialog.getByRole('button',{name:/삼성전자/}).click();
 await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible({timeout:20000});
 await page.getByRole('button',{name:'목록으로 돌아가기',exact:true}).click();
 await expect(industry.getByRole('button',{name:'전체 산업',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(industry.getByRole('navigation',{name:'산업 분류 경로'})).toContainText('희귀 소재');
 await expect(item).toBeVisible();
 await page.getByRole('button',{name:'글자 크기',exact:true}).click();
 await page.getByRole('button',{name:'더 크게',exact:true}).click();
 await page.getByRole('button',{name:'이 크기로 보기'}).click();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await industry.getByRole('button',{name:'순위',exact:true}).click();
 await expect(industry.getByRole('combobox',{name:'산업 기간'})).toHaveValue('1y');
});

test('stock warning failures keep prices, retry and become silent when ready and empty',async({page})=>{
 let phase='ready',calls=0;
 await page.route('**/api/market/v1/securities/005930/warnings',route=>{calls++;return route.fulfill(phase==='http-error'?{status:503,json:{detail:'fixture failure'}}:{json:{status:phase,rows:phase==='stale'?[{name:'투자주의',exchange:'KRX'}]:[]}});});
 await page.goto('/stocks?code=005930');
 const status=page.getByRole('status',{name:'종목 유의사항 조회 상태'});
 await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible({timeout:20000});
 await expect.poll(()=>calls).toBeGreaterThan(0);
 await expect(status).toHaveCount(0);
 phase='error';await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
 await expect(status).toContainText('불러오지 못했어요');
 await expect(page.getByRole('heading',{name:'삼성전자',exact:true})).toBeVisible();
 phase='stale';await page.getByRole('button',{name:'유의사항 다시 시도'}).click();
 await expect(status).toContainText('마지막 자료');
 await expect(page.locator('.mk-notice')).toContainText('투자주의');
 phase='http-error';const before=calls;await page.getByRole('button',{name:'유의사항 다시 시도'}).click();
 await expect.poll(()=>calls).toBeGreaterThan(before);
 await expect(page.locator('.mk-notice')).toContainText('투자주의');
 phase='ready';await page.getByRole('button',{name:'유의사항 다시 시도'}).click();
 await expect(status).toHaveCount(0);await expect(page.locator('.mk-notice')).toHaveCount(0);
 phase='pending';const pendingCalls=calls;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
 await expect.poll(()=>calls).toBeGreaterThan(pendingCalls);
 await expect(status).toHaveCount(0);
 await expect(status).toContainText('확인이 늦어지고 있어요',{timeout:12000});
 phase='ready';await page.getByRole('button',{name:'유의사항 다시 시도'}).click();
 await expect(status).toHaveCount(0);
});

test('listing scope and empty market investor history are explicit',async({page})=>{
 await page.route('**/api/market/v1/indices/KOSPI/investors?*',route=>route.fulfill({json:{status:'no_data',rows:[]}}));
 await page.route('**/api/market/v1/listings?*',route=>route.fulfill({json:{status:'ready',rows:[]}}));
 await page.goto('/stocks');
 await page.getByText('기간별 순위 · 시장 수급 · 금리·환율',{exact:true}).click();
 await page.getByRole('button',{name:'시장 수급',exact:true}).click();
 await expect(page.getByText('조회된 시장 수급이 없어요.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'상장 현황',exact:true}).click();
 const market=page.getByRole('combobox',{name:'시장',exact:true});
 await expect(market).toBeVisible();
 await expect(market.locator('option')).toHaveText(['KOSPI','KOSDAQ']);
});
