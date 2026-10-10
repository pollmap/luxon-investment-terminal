import {test,expect} from '@playwright/test';

// Local interaction contract, synthetic evidence never promoted to production.
test('document search is explicit, works with Enter, and preserves source links',async({page})=>{
 await page.routeWebSocket('**/api/market/v1/stream',()=>{});
 const research:string[]=[];
 await page.route('**/api/market/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/market/v1','');
  let result:unknown={status:'ready',rows:[]};
  if(path==='/research/search'){
   research.push(url.searchParams.get('q')||'');
   result={status:'ready',mode:'lexical_only',fallback_reason:'embedding_not_configured',available_documents:1,revision:'fixture_non_production',items:[{
    id:'a'.repeat(64),revision:'b'.repeat(64),title:'연구용 사업보고서',excerpt:'반도체 설비투자에 관한 검증용 구절',code:'005930',company:'삼성전자',source_url:'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261011000001',published_at:'2026-10-01',section:'사업의 내용',page:12,
   }]};
   if(research.length>1)result={status:'ready',mode:'lexical_only',available_documents:1,coverage:{index:'context_titles_only'},revision:'fixture_titles_only',items:[{id:'c'.repeat(64),revision:'d'.repeat(64),title:'연구용 제목 검색',excerpt:'연구용 제목 검색',source_url:'https://example.com/research',section:'제목'}]};
  }else if(path==='/search-index')result={rows:[{code:'005930',name:'삼성전자',market:'KOSPI',tokens:['삼성전자','005930']}]};
  else if(path==='/screener')result={rows:[],total:0};
  else if(path==='/overview')result={indices:[],breadth:{up:0,down:0,flat:0},total:0,value:0,sectors:[],filings:{rows:[],status:'ready'}};
  await route.fulfill({json:result});
 });
 await page.goto('/stocks');
 await expect(page.getByRole('heading',{name:'오늘의 시장',exact:true})).toBeVisible();
 const input=page.getByRole('combobox',{name:'종목명 또는 코드 검색'});
 await input.fill('반도체');
 await page.getByRole('button',{name:'자료',exact:true}).click();
 await expect(page.getByRole('button',{name:'자료 검색',exact:true})).toBeVisible();
 const query=page.getByRole('textbox',{name:'근거 자료 검색어'});
 await query.fill('반도체 설비투자');
 expect(research).toEqual([]);
 await query.press('Enter');
 const panel=page.getByRole('region',{name:'근거 자료 검색'});
 await expect(panel).toContainText('키워드 검색');
 await expect(panel.getByRole('link',{name:'연구용 사업보고서 ↗'})).toHaveAttribute('href',/^https:\/\/dart\.fss\.or\.kr\//);
 await expect(panel).toContainText('사업의 내용 · 12쪽');
 expect(research).toEqual(['반도체 설비투자']);
 await query.fill('새 검색어');
 await expect(panel).toContainText('연구용 사업보고서');
 expect(research).toHaveLength(1);
 await panel.getByRole('button',{name:'자료 검색',exact:true}).click();
 await expect.poll(()=>research.length).toBe(2);
 await expect(panel).toContainText('문서 제목만 검색해요.');
 await expect(panel.getByText('연구용 제목 검색', {exact:false})).toHaveCount(1);
 await expect(panel.locator('article small')).toHaveCount(0);
 await query.press('Escape');
 await expect(panel).not.toBeVisible();
 await expect(query).toBeFocused();
});
