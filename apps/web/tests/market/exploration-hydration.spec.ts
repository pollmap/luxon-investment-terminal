import {test,expect} from '@playwright/test';

// Delay executable chunks while allowing the real server-rendered HTML/CSS.
// This reproduces the interval where buttons were visible before handlers and
// session preferences were ready; no application-only test switch is needed.
for(const width of [360,1440])test(`exploration waits for hydration and restores selection at ${width}px`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:900});
 await page.addInitScript(()=>{
  sessionStorage.setItem('korea-stocks:exploration:v1',JSON.stringify({tab:'테마',query:'',limit:12,sectorQuery:'',sectorLimit:8,sectorSort:'value'}));
  sessionStorage.setItem('korea-stocks:industries:v1',JSON.stringify({kind:'TOP_GAINERS',duration:'1y',filter:'',limit:10}));
 });
 let releaseScripts!:()=>void;
 const scriptsReady=new Promise<void>(resolve=>{releaseScripts=resolve;});
 await page.route('**/_next/**',async route=>{if(route.request().resourceType()==='script')await scriptsReady;await route.continue();});
 await page.route('**/api/market/v1/themes',route=>route.fulfill({json:{rows:[]}}));
 await page.route('**/api/market/v1/sectors/rankings?*',route=>route.fulfill({json:{status:'ready',rows:[{id:'chips',name:'반도체',rank:1,change:1,value:100000} ]}}));
 try{
  await page.goto('/stocks',{waitUntil:'commit'});
  const explorer=page.locator('.mk-explorer'),tab=explorer.getByRole('tab',{name:'산업',exact:true});
  await expect(tab).toBeVisible();await expect(explorer).toHaveAttribute('aria-busy','true');await expect(tab).toBeDisabled();
  await tab.scrollIntoViewIfNeeded();
  const point=await tab.boundingBox();expect(point).not.toBeNull();await page.mouse.click(point!.x+point!.width/2,point!.y+point!.height/2);
  await expect(tab).toHaveAttribute('aria-selected','false');
  const styles=await explorer.locator(':scope > fieldset').evaluate(el=>{const s=getComputedStyle(el);return {border:s.borderTopWidth,margin:s.margin,padding:s.padding,minWidth:s.minWidth,disabled:(el as HTMLFieldSetElement).disabled};});
  expect(styles).toEqual({border:'0px',margin:'0px',padding:'0px',minWidth:'0px',disabled:true});
  await page.screenshot({path:testInfo.outputPath(`initializing-${width}.png`)});
  releaseScripts();
  await expect(explorer).toHaveAttribute('aria-busy','false');await expect(tab).toBeEnabled();
  await expect(explorer.getByRole('tab',{name:'테마',exact:true})).toHaveAttribute('aria-selected','true');
  await tab.click();
  const industry=page.getByRole('region',{name:'토스 산업 분류'});
  await expect(industry).toBeVisible();await expect(industry).toHaveAttribute('aria-busy','false');
  await expect(industry.getByRole('combobox',{name:'산업 순위'})).toHaveValue('TOP_GAINERS');
  await expect(industry.getByRole('combobox',{name:'산업 기간'})).toHaveValue('1y');
  await expect(industry.getByRole('button',{name:'전체 산업',exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await industry.scrollIntoViewIfNeeded();await page.screenshot({path:testInfo.outputPath(`ready-${width}.png`)});
 }finally{releaseScripts();}
});
