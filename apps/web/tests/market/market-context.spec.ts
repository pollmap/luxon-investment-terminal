import { test, expect } from "@playwright/test";

// Contract fixtures verify date isolation and user recovery; they are not live market evidence.
test("historical market never reuses today's rows for an uncollected date", async ({page}) => {
  await page.setViewportSize({width:1440,height:900});
  await page.route("**/api/market/v1/news?*", route => route.fulfill({json:{status:"ready",has_more:false,rows:[{title:"시장 기록 검증 기사",url:"https://example.com/fixture-news",published_at:"2026-01-02T10:00:00+09:00",publisher:"검증 자료"}]}}));
  let fail = false;
  const requested: string[] = [];
  await page.route("**/api/market/v1/market-brief**", async route => {
    const date=new URL(route.request().url()).searchParams.get("date");
    requested.push(date || "latest");
    if(fail) return route.fulfill({status:503,json:{detail:"fixture outage"}});
    return route.fulfill({json:{status:date?"not_collected":"ready",date:date||"2026-01-02",available_dates:["2026-01-02"],groups:date?[]:[{venue:"KRX",covered:1,value:1000000,breadth:{up:1,down:0,flat:0,unknown:0},rows:[{code:"005930",name:"날짜 검증 종목",price:12345,change:1,value:1000000}]}],news:{status:"ready",rows:[]}}});
  });
  await page.goto("/stocks");
  await page.getByRole("button",{name:"날짜별 시장",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"날짜별 시장"});
  await expect(dialog.getByRole("button",{name:"날짜 검증 종목"})).toBeVisible();
  await dialog.getByLabel("날짜",{exact:true}).fill("2026-01-01");
  await expect(dialog).toContainText("이 날짜의 시장 기록은 아직 없어요.");
  await expect(dialog.getByRole("button",{name:"날짜 검증 종목"})).toHaveCount(0);
  expect(requested.at(-1)).toBe("2026-01-01");
  fail=true;
  await dialog.getByRole("button",{name:"최근 기록"}).click();
  await expect(dialog.getByRole("button",{name:"다시 시도"})).toBeVisible();
  fail=false;
  await dialog.getByRole("button",{name:"다시 시도"}).click();
  await expect(dialog.getByRole("button",{name:"날짜 검증 종목"})).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button",{name:"날짜별 시장",exact:true})).toBeFocused();
});
