import {test,expect} from '@playwright/test';

test('actual metric switching retains the chart, date, and source-backed numbers',async({page})=>{
 await page.goto('/stocks?code=005930');
 await page.getByRole('tab',{name:'기업 분석',exact:true}).click();
 const panel=page.getByRole('region',{name:'실적과 주가',exact:true});
 await expect(panel.getByLabel('가치평가 지표')).toHaveValue('EPS');
 await expect(panel.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 await panel.getByRole('button',{name:'이전 날짜',exact:true}).click();
 const selected=await panel.locator('.mk-valuation-reading>strong').textContent();
 const canvas=await panel.locator('.mk-financial-canvas canvas').first().elementHandle();
 const response=page.waitForResponse(r=>r.url().includes('/analysis/valuation?')&&r.url().includes('metric=OCF')&&r.ok());
 await panel.getByLabel('가치평가 지표').selectOption('OCF');
 const json=await (await response).json();
 expect(json.metric).toBe('OCF');expect(json.periods.length).toBeGreaterThan(0);
 await expect(panel.locator('.mk-valuation-reading>strong')).toHaveText(selected!);
 expect(await canvas!.evaluate(node=>node.isConnected)).toBe(true);
 await panel.getByLabel('이익 비교 배수').fill('8');
 await expect(panel.locator('.mk-legend-reference')).toContainText('영업현금흐름/주 × 8배');
 await expect(panel.getByRole('columnheader',{name:'영업현금흐름/주 · 원/주'})).toBeVisible();
 await panel.getByLabel('가치평가 지표').selectOption('EBITDA');
 await expect(panel.getByRole('columnheader',{name:'EBITDA/주 · 원/주'})).toBeVisible();
 await page.reload();
 await expect(page.getByLabel('가치평가 지표')).toHaveValue('EBITDA');
});

test('an ineligible positive fact is never reconstructed as a comparison',async({page})=>{
 await page.route('**/analysis/valuation?**',async route=>{
   const upstream=await route.fetch(); const value=await upstream.json();
   value.availability.comparison=false;
   for(const point of value.series){point.metric_value=100;point.reference_price=null;point.comparison_eligible=false;}
   await route.fulfill({response:upstream,json:value});
 });
 await page.goto('/stocks?code=005930');
 await page.getByRole('tab',{name:'기업 분석',exact:true}).click();
 const panel=page.getByRole('region',{name:'실적과 주가',exact:true});
 await expect(panel.locator('.mk-financial-canvas canvas').first()).toBeVisible();
 await panel.getByLabel('이익 비교 배수').fill('20');
 await expect(panel.locator('.mk-legend-reference')).toHaveCount(0);
});

test('negative operating cash flow remains visible without inventing a positive-multiple line',async({page})=>{
 await page.goto('/stocks?code=005380');
 await page.getByRole('tab',{name:'기업 분석',exact:true}).click();
 const panel=page.getByRole('region',{name:'실적과 주가',exact:true});
 await expect(panel.getByLabel('가치평가 지표')).toBeVisible();
 await panel.getByLabel('가치평가 지표').selectOption('OCF');
 await expect(panel.getByText('0 이하인 실적은 배수 비교선에서 제외돼요.',{exact:true})).toBeVisible();
 await expect(panel.locator('.mk-legend-reference')).toHaveCount(0);
 await expect(panel.getByText('주식 수 기준은 2026-06-30까지 확인돼요.',{exact:true})).toBeVisible();
});

for(const width of [1024,1280,1440,1920])test(`analysis fits ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:1000});
 await page.goto('/stocks?code=005930');
 await page.getByRole('tab',{name:'기업 분석',exact:true}).click();
 await expect(page.getByLabel('가치평가 지표')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('dark and larger text keep metric controls and the reading panel usable',async({page})=>{
 await page.setViewportSize({width:1024,height:900});
 await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주'));
 await expect(page.getByLabel('가치평가 지표')).toBeVisible();
 await page.getByRole('button',{name:'화면 설정',exact:true}).click();
 await page.getByRole('button',{name:'다크',exact:true}).click();
 await page.getByRole('dialog').getByRole('button',{name:/^글자 크기/}).click();
 await page.getByRole('button',{name:'더 크게',exact:true}).click();
 await page.getByRole('button',{name:'이 크기로 보기',exact:true}).click();
 await page.getByLabel('가치평가 지표').selectOption('OCF');
 await page.getByLabel('이익 비교 배수').fill('8');
 await expect(page.locator('.mk-legend-reference')).toContainText('영업현금흐름/주');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('download uses the same validated values as the comparison chart',async({page})=>{
 await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주')+'&v_metric=OCF&v_multiple=8');
 await expect(page.locator('.mk-legend-reference')).toContainText('8배');
 await page.getByText('날짜별 숫자 보기',{exact:true}).click();
 const downloading=page.waitForEvent('download');
 await page.getByRole('button',{name:'CSV 저장',exact:true}).click();
 const download=await downloading;const stream=await download.createReadStream();
 let csv='';for await(const chunk of stream!)csv+=chunk.toString();
 expect(download.suggestedFilename()).toContain('005930-OCF');
 const rows=csv.trim().split('\r\n').slice(1).map(line=>line.slice(1,-1).split('","'));
 const eligible=rows.filter(row=>row[4]!=='');expect(eligible.length).toBeGreaterThan(0);
 for(const row of eligible)expect(Number(row[4])).toBeCloseTo(Number(row[2])*8,5);
 expect(rows.filter(row=>row[2]===''&&row[4]!=='')).toHaveLength(0);
});
