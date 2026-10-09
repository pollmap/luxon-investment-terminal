import { test, expect } from '@playwright/test';

test('base address opens the public dashboard without authentication routes', async ({page}) => {
 await page.goto('/');
 await expect(page).toHaveURL(/\/stocks$/);
 await expect(page.getByRole('heading',{name:'오늘의 시장',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:/로그인|내 계정/})).toHaveCount(0);
 expect((await page.request.get('/api/auth/session')).status()).toBe(404);
 expect((await page.request.get('/api/pf/session')).status()).toBe(404);
});

test('index periods and theme membership lead into source-backed company data', async ({page}) => {
 await page.goto('/stocks');
 await page.getByRole('button',{name:'코스피 차트 보기',exact:true}).click();
 const chartResponse = page.waitForResponse(response => response.url().includes('/api/market/v1/indices/KOSPI/chart?period=year') && response.ok());
 await page.getByRole('button',{name:'1년',exact:true}).click();
 const response = await chartResponse;
 const series = await response.json();
 expect(['ready','stale']).toContain(series.status);
 expect(series.points.length).toBeGreaterThan(1);
 expect(series.points.at(-1).time).toBeTruthy();
 await expect(page.locator('.mk-pulse-detail svg[role="img"]')).toBeVisible();
 await expect(page.locator('.mk-pulse-detail svg[role="img"]')).toHaveAttribute('aria-label',new RegExp(`코스피, ${series.points.length}개 구간`));
 await page.getByRole('tab',{name:'테마',exact:true}).click();
 await page.getByRole('textbox',{name:'테마 검색',exact:true}).fill('HBM');
 await page.locator('.mk-theme-grid button').first().click();
 await expect(page.getByRole('dialog').locator('.mk-member-list button')).not.toHaveCount(0,{timeout:20000});
 await page.getByRole('dialog').getByRole('button').filter({hasText:'SK하이닉스'}).click();
 await expect(page.getByRole('heading',{name:'SK하이닉스',exact:true})).toBeVisible();
 await expect(page.locator('.mk-chart-panel svg[role="img"]')).toBeVisible();
 await page.getByRole('tab',{name:'재무·지표',exact:true}).click();
 await expect(page.getByRole('heading',{name:'재무제표 · 주요 지표',exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'실적의 흐름',exact:true})).toBeVisible();
});

test('major-account data shows only its available financial statement tabs',async({page})=>{
 await page.route('**/api/market/v1/securities/000660/financials', route=>route.fulfill({json:{reports:[],indicators:[]}}));
 await page.route(/\/api\/market\/v1\/securities\/000660(?:\?.*)?$/,async route=>{
  const response=await route.fetch();
  const payload=await response.json();
  payload.official={
   company:{corp_name:'SK하이닉스'},
   major_accounts_received_at:'2026-09-25T05:00:00Z',
   major_accounts_period:'2025',
   major_accounts_status:'ready',
   statements:[{name:'매출액',account_id:'IS:매출액',statement:'IS',basis:'CFS',period:'2025',current:66193000000000,previous:66000000000000,prior:32000000000000,unit:'KRW',receipt:'00000000000000'}]
  };
  await route.fulfill({response,contentType:'application/json',body:JSON.stringify(payload)});
 });
 await page.goto('/stocks?code=000660');
 await page.getByRole('tab',{name:'재무·지표',exact:true}).click();
 await expect(page.getByRole('heading',{name:'기본 재무 계정',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'손익계산서',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.getByRole('button',{name:'현금흐름표',exact:true})).toHaveCount(0);
 const majorAccountsPanel=page.locator('.mk-panel').filter({has:page.getByRole('heading',{name:'기본 재무 계정',exact:true})});
 await expect(majorAccountsPanel.locator('tbody tr').filter({hasText:'매출액'})).toBeVisible();
});

test('company profile adds DART ownership and capital data without cluttering the first view',async({page})=>{
 await page.route(/\/api\/market\/v1\/securities\/005930(?:\?.*)?$/,async route=>{
  const response=await route.fetch();
  const payload=await response.json();
  payload.official={
   company:{corp_name:'삼성전자'},
   report_facts:{year:'2025',report_code:'11011',sections:{
    shareholders:{status:'ready',year:'2025',rows:[{name:'최대주주',relationship:'본인',stock_type:'보통주',beginning_shares:100000,ending_shares:120000,ending_ratio:10.25}]},
    shares:{status:'ready',year:'2025',rows:[{stock_type:'보통주',issued:500000,treasury:50000,circulating:450000}]},
    dividends:{status:'ready',year:'2025',rows:[{category:'현금배당',stock_type:'보통주',current:1500,previous:1200}]},
    employees:{status:'ready',year:'2025',rows:[{division:'합계',gender:'계',regular:1000,contract:100,total:1100,average_tenure:8.5}]},
    executives:{status:'ready',year:'2025',rows:[{name:'홍길동',position:'대표이사',registered:'등기임원',responsibility:'대표이사',term_end:'2027-03-31'}]},
    minority_shareholders:{status:'ready',year:'2025',rows:[{category:'소액주주',holders:100,total_holders:120,holder_ratio:83.3,shares:900,total_shares:1000,share_ratio:90}]},
    holder_changes:{status:'ready',year:'2025',rows:[{date:'2025.03.01',name:'최대주주',shares:200,ratio:20,cause:'장내 취득'}]},
    auditor:{status:'ready',year:'2025',rows:[{auditor:'감사법인',opinion:'적정',emphasis:'강조사항',key_audit_matters:'핵심감사사항'}]},
    treasury:{status:'ready',year:'2025',rows:[{method:'직접 취득',stock_type:'보통주',beginning:100,acquired:20,disposed:10,cancelled:5,ending:105}]},
    investments:{status:'ready',year:'2025',rows:[{company:'삼성전기',purpose:'계열회사',ending_shares:300,ending_ratio:30,ending_value:5000,net_income:200}]}
   }}
  };
  await route.fulfill({response,contentType:'application/json',body:JSON.stringify(payload)});
 });
 await page.goto('/stocks');
 await page.getByRole('button',{name:'종목명 또는 코드 검색 Ctrl K'}).click();
 await page.getByRole('textbox',{name:'종목명 또는 코드'}).fill('005930');
 await expect(page.getByRole('dialog').getByRole('button',{name:/삼성전자.*005930/})).toBeVisible();
 await page.getByRole('dialog').getByRole('button',{name:/삼성전자.*005930/}).click();
 await page.getByRole('tab',{name:'기업·주주',exact:true}).click();
 const facts=page.locator('.mk-company-report-facts');
 await expect(facts.getByRole('heading',{name:'회사 공시 정보'})).toBeVisible();
 await expect(facts.getByText('10.25%',{exact:true})).toBeVisible();
 await expect(facts.getByText('450,000주',{exact:true})).toBeVisible();
 await expect(facts.locator('summary').filter({hasText:'배당 내역'})).toBeVisible();
 await expect(facts.locator('summary').filter({hasText:'직원 현황'})).toBeVisible();
 await expect(facts.locator('summary').filter({hasText:'소액주주 현황'})).toContainText('100명');
 await expect(facts.locator('summary').filter({hasText:'최대주주 변동'})).toBeVisible();
 await expect(facts.locator('summary').filter({hasText:'회계감사 의견'})).toContainText('적정');
 await expect(facts.locator('summary').filter({hasText:'자기주식 내역'})).toContainText('기말 105주');
 await expect(facts.locator('summary').filter({hasText:'타법인 출자'})).toContainText('1개 법인');
 await expect(facts.locator('.mk-company-fact-disclosure[open]')).toHaveCount(0);
 await expect(facts.getByText(/출처|갱신 시각|원천 한도|제공 한도|사업보고서 기준/)).toHaveCount(0);
});

test('public market works without accounts and stores favorites on this device',async({page,browser})=>{
 await page.goto('/stocks');
 await expect(page.getByRole('button',{name:/로그인|내 계정/})).toHaveCount(0);
 await page.getByRole('button',{name:'삼성전자 관심종목 추가',exact:true}).click();
 await page.reload();
 await expect(page.getByRole('button',{name:'삼성전자 관심종목 해제',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'화면 설정',exact:true}).click();
 await expect(page.getByRole('dialog').getByText(/계정|로그인|동기화|비밀번호/)).toHaveCount(0);
 await page.getByRole('button',{name:'닫기',exact:true}).click();
 const second=await browser.newContext({baseURL:process.env.MARKET_WEB_URL||'http://127.0.0.1:3210'});
 try {
  const p=await second.newPage();await p.goto('/stocks');
  await expect(p.getByRole('button',{name:'로그인',exact:true})).toHaveCount(0);
  await expect(p.getByRole('button',{name:'삼성전자 관심종목 추가',exact:true})).toBeVisible();
 } finally {await second.close();}
});

test('brokerage aggregates show recognizable broker names',async({page})=>{
 const data=await (await page.request.get('/api/market/v1/securities/005930')).json();
 const top=[...data.detail.brokers].sort((a,b)=>(b.buy??-1)-(a.buy??-1))[0];
 await page.goto('/stocks?code=005930');
 await page.getByRole('tab',{name:'수급·거래원',exact:true}).click();
 await page.getByRole('button',{name:'증권사별',exact:true}).click();
 await expect(page.locator('.mk-broker-ranks li').first()).toContainText(top.name);
 await page.getByText('증권사별 전체 표 보기',{exact:true}).click();
 await expect(page.locator('.mk-activity-table tbody tr')).toHaveCount(data.detail.brokers.length);
 await expect(page.getByText(/제공처|원천 한도|갱신 시각/)).toHaveCount(0);
});
