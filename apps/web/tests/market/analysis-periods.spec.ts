import {test,expect,type Page} from '@playwright/test';

// Synthetic UI contract only. All data and subscriptions stay inside Playwright.
async function fixture(page:Page){
  await page.addInitScript(()=>Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true}));
  await page.routeWebSocket('**/api/market/v1/stream',()=>{});
  const stock={code:'005930',name:'삼성전자',market:'KOSPI',price:500,change:1,volume:10,value:1000,cap:10000,as_of:'2026-10-08T15:30:00+09:00',received_at:'2026-10-08T15:30:00+09:00',market_status:'CLOSE',facts:{}};
  const financials=['2021','2023','2024'].flatMap(period=>['매출액','영업이익','당기순이익'].map((name,i)=>({period,name,value:100-i*10,frequency:'annual',basis:'CFS',estimate:false,unit:'억원',source_trace:{source:'fixture_non_production',source_url:'https://example.invalid',period,unit:'억원',method:'fixture',formula:''}})));
  const definition={code:'EPS',label:'기본 EPS',group:'이익',default_multiple:15,supports_ttm:true};
  await page.route('**/api/market/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/market/v1','');
    let data:unknown={status:'ready',rows:[]};
    if(path==='/metrics')data={};
    else if(path==='/search-index')data={rows:[{code:stock.code,name:stock.name,market:stock.market,tokens:['005930','삼성전자']}]};
    else if(path==='/overview')data={indices:[],breadth:{up:1,down:0,flat:0},total:1,value:1000,sectors:[],filings:{rows:[],status:'ready'}};
    else if(path==='/screener')data={rows:[stock],total:1};
    else if(path==='/securities/005930')data={security:stock,detail:{financials,flows:[],facts:{}},prices:{rows:[]},filings:{rows:[]},official:{company:{},statements:[]}};
    else if(path.endsWith('/quote'))data=stock;
    else if(path.endsWith('/financials/timeline'))data={rows:financials};
    else if(path.endsWith('/analysis/metrics'))data={metrics:[definition]};
    else if(path.endsWith('/analysis/valuation'))data={code:'005930',metric:'EPS',metric_definition:definition,multiple:15,basis:'CFS',frequency:'annual',share_basis:{per_share_basis_end:'2024-04-01'},series:[
      {date:'2024-01-01',price:100,metric_value:10,reference_price:null,comparison_eligible:false},
      {date:'2024-02-01',price:200,metric_value:10,reference_price:150,comparison_eligible:true},
      {date:'2024-03-01',price:300,metric_value:null,reference_price:null,comparison_eligible:false},
      {date:'2024-04-01',price:400,metric_value:20,reference_price:300,comparison_eligible:true},
      {date:'2024-05-01',price:500,metric_value:30,reference_price:null,comparison_eligible:false},
    ],periods:[],summary:{representative_multiple:null,monthly_observations:0},availability:{comparison:true,reasons:[]},ttm:{rows:[]}};
    await route.fulfill({json:data});
  });
}

for(const width of [390,768,1440])test(`fiscal gaps and verified comparison range remain usable at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await fixture(page);
  await page.goto('/stocks?code=005930&tab='+encodeURIComponent('기업·주주'));
  const earnings=page.locator('.mk-earnings'),chart=page.getByRole('region',{name:'실적과 주가',exact:true});
  await expect(earnings.getByRole('columnheader')).toHaveText(['항목','2022','2023','2024']);
  await expect(earnings.getByLabel('2022 매출액 자료 없음')).toHaveText('—');
  await expect(earnings).toContainText('2024 회계연도까지');
  await earnings.getByRole('button',{name:'전체',exact:true}).click();
  await expect(earnings.getByRole('columnheader')).toHaveText(['항목','2021','2023','2024']);
  await earnings.getByRole('button',{name:'최근 3년',exact:true}).click();
  await expect(earnings.getByRole('columnheader')).toHaveText(['항목','2022','2023','2024']);
  await expect(chart.getByLabel('비교 가능한 실적 구간')).toHaveText('비교 가능한 구간 2024-02-01 ~ 2024-04-01 · 중간 공백 있음');
  await chart.getByRole('button',{name:'이전 날짜',exact:true}).click();
  const selected=await chart.locator('.mk-valuation-reading>strong').textContent();
  await chart.getByLabel('이익 비교 배수').fill('20');
  await expect(chart.locator('.mk-valuation-reading>strong')).toHaveText(selected!);
  await expect(chart.getByLabel('비교 가능한 실적 구간')).toContainText('2024-02-01 ~ 2024-04-01');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  if(width===1440){
    await earnings.screenshot({path:testInfo.outputPath('fixture-earnings.png')});
    await chart.screenshot({path:testInfo.outputPath('fixture-comparison.png')});
  }
});
