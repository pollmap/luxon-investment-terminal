import { test, expect } from "@playwright/test";

test("all sectors are searchable and open a filtered list", async ({ page }) => {
  await page.goto("/stocks");
  await expect(page.getByRole("button", { name: /업종 더 보기/ })).toBeVisible({timeout:15000});
  await page.getByRole("combobox", { name: "업종 정렬" }).selectOption("name");
  await page.getByRole("textbox", { name: "업종 검색", exact: true }).fill("반도체");
  const sector = page.locator(".mk-sector-bars button").first();
  await expect(sector).toContainText("반도체");
  await sector.click();
  await expect(page.locator(".mk-filter-chips")).toContainText("반도체");
  await expect(page.locator(".mk-stock-table tbody tr").first()).toBeVisible();
});

test("comparison uses a common baseline and works on mobile", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({width: 390, height: 844});
  await page.goto("/stocks?page=compare&compare=005930,000660");
  await expect(page.getByRole("heading", {name: "주가 수익률 비교"})).toBeVisible();
  await expect(page.locator(".mk-comparison-chart polyline")).toHaveCount(2, {timeout:15000});
  const slider = page.getByRole("slider", {name: "비교 차트 날짜"});
  await slider.focus();
  await page.keyboard.press("Home");
  await expect(page.locator(".mk-comparison-legend strong").first()).toHaveText("0.00%");
  await expect(page.locator(".mk-comparison-legend strong").last()).toHaveText("0.00%");
  await page.getByRole("button", {name: "3개월", exact: true}).click();
  await expect(page.locator(".mk-comparison-chart polyline")).toHaveCount(2, {timeout:15000});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await expect(page.locator('.mk-compare th').filter({hasText: '매출액'})).toContainText('억원');
  await expect(page.locator('.mk-compare th').filter({hasText: '외국인 순매수'})).toContainText('주');
  await page.screenshot({path: "../../docs/screenshots/comparison-mobile.png", fullPage: true});
  expect(errors).toEqual([]);
});

test("guest preferences can be exported and restored without removing current favorites", async ({ page }) => {
  await page.goto("/stocks");
  await page.getByRole("button", {name: "화면 설정", exact: true}).click();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", {name: "백업 파일 저장"}).click();
  expect((await downloadEvent).suggestedFilename()).toContain("관심종목.json");
  await page.getByLabel("백업 파일 가져오기", {exact:true}).setInputFiles({
    name: "fixture.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({
      format: "korea-stocks-preferences", version: 1,
      preferences: {favorites: ["005930", "000660"], views: [], groups: {"검수 그룹": ["005930"]}},
    })),
  });
  await expect(page.getByText("관심종목과 저장한 보기를 추가했습니다.", {exact:true})).toBeVisible();
  await page.getByRole("button", {name:"닫기", exact:true}).click();
  await page.getByRole("navigation", {name:"주 메뉴", exact:true}).getByRole("button", {name:"관심종목", exact:true}).click();
  await expect(page.getByRole("button", {name:"삼성전자 005930 KOSPI",exact:true})).toBeVisible();
  await expect(page.getByRole("button", {name:"SK하이닉스 000660 KOSPI",exact:true})).toBeVisible();
  const exportEvent = page.waitForEvent("download");
  await page.getByRole("button", {name:"CSV 내려받기",exact:true}).click();
  expect((await exportEvent).suggestedFilename()).toContain(".csv");
});

test("detail polls a small quote instead of refetching financials", async ({ page }) => {
  const full: string[] = [], quotes: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/market/v1/securities/005930") full.push(path);
    if (path === "/api/market/v1/securities/005930/quote") quotes.push(path);
  });
  await page.goto("/stocks?code=005930");
  await expect(page.getByRole("heading", {name:"삼성전자",exact:true})).toBeVisible();
  const initial = full.length;
  await expect.poll(() => quotes.length, {timeout:12000}).toBeGreaterThan(0);
  expect(full).toHaveLength(initial);
});
