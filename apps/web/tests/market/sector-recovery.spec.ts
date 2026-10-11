import { test, expect } from "@playwright/test";

// Fixtures exercise interaction and failure recovery, not live-data coverage.
const ranking = { status: "ready", rows: [
  { id: "semiconductor", name: "반도체", rank: 1, change: 1.79, value: 1300000000000 },
  { id: "automobile", name: "자동차", rank: 2, change: null, value: null },
] };

for (const width of [360, 1440]) {
  test(`industry filters, partial failure, stable ranking and keyboard return at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    let rankingCalls = 0, failRanking = false, failMembers = true;
    const durations: string[] = [];
    await page.route("**/api/market/v1/sectors/rankings?*", async route => {
      rankingCalls++;
      durations.push(new URL(route.request().url()).searchParams.get("duration") || "");
      await route.fulfill(failRanking ? { status: 503, json: { detail: "fixture outage" } } : { json: ranking });
    });
    await page.route("**/api/market/v1/sectors/semiconductor", route => route.fulfill({ json: {
      status: "ready", summary: "검증용 산업 설명", performance: [{ duration: "1d", change: 1.79 }, { duration: "1y", change: null }],
    } }));
    await page.route("**/api/market/v1/sectors/semiconductor/stocks", route => route.fulfill(failMembers
      ? { status: 503, json: { detail: "fixture member outage" } }
      : { json: { status: "ready", rows: [{ code: "005930", name: "삼성전자", market: "KOSPI", cap: 400000000000000, value: null }] } }));

    await page.goto("/stocks");
    const explorer = page.locator(".mk-explorer");
    await expect(explorer.getByRole("tab", { name: "업종", exact: true })).toBeEnabled();
    await explorer.getByRole("tab", { name: "업종", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(explorer.getByRole("tab", { name: "산업", exact: true })).toBeFocused();
    await expect(explorer.getByRole("tab", { name: "산업", exact: true })).toHaveAttribute("aria-selected", "true");
    const industry = page.getByRole("region", { name: "토스 산업 분류" });
    const first = industry.locator(".mk-industry-list button").first();
    await expect(first).toContainText("반도체");
    await expect(first).toContainText("+1.79%");
    await expect(industry.locator(".mk-industry-list button").last()).toContainText("—");
    await industry.getByRole("combobox", { name: "산업 기간" }).selectOption("1y");
    await expect.poll(() => durations.at(-1)).toBe("1y");
    await expect(first).toBeEnabled();
    const before = rankingCalls;
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(industry.getByRole("button", { name: "최신 순위 반영" })).toBeEnabled();
    expect(rankingCalls).toBe(before);

    failRanking = true;
    await industry.getByRole("button", { name: "최신 순위 반영" }).click();
    await expect(industry.getByRole("status")).toContainText("마지막 자료");
    await expect(first).toContainText("반도체");
    failRanking = false;
    await industry.getByRole("button", { name: "다시 시도" }).click();
    await expect(industry.getByRole("status")).toHaveCount(0);

    await first.click();
    const dialog = page.getByRole("dialog", { name: "반도체" });
    await expect(dialog).toContainText("검증용 산업 설명");
    await expect(dialog.getByRole("status")).toContainText("일부 자료를 불러오지 못했어요");
    await expect(dialog.getByText("종목을 가져오는 중이에요.")).toHaveCount(0);
    failMembers = false;
    await dialog.getByRole("button", { name: "다시 시도" }).click();
    await expect(dialog.getByRole("button", { name: /삼성전자/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(first).toBeFocused();
    await expect(dialog).toHaveCount(0);
    await first.click();
    await dialog.getByRole("button", { name: /삼성전자/ }).click();
    await expect(page.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "목록으로 돌아가기", exact: true }).click();
    await expect(explorer.getByRole("tab", { name: "산업", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(industry.getByRole("combobox", { name: "산업 기간" })).toHaveValue("1y");
    await expect(first).toContainText("반도체");
    expect(errors).toEqual([]);
  });
}

test("initial detail failure retries and rejects another security's response", async ({ page, request }) => {
  const response = await request.get("/api/market/v1/securities/005930?view=summary");
  expect(response.ok()).toBeTruthy();
  const source = await response.json();
  let calls = 0, allowCorrect = false;
  await page.route(/\/api\/market\/v1\/securities\/005930\?view=summary$/, route => {
    calls++;
    return route.fulfill(!allowCorrect
      ? { json: { ...source, security: { ...source.security, code: "000660", name: "잘못된 종목" } } }
      : { json: source });
  });
  await page.goto("/stocks?code=005930");
  await expect(page.getByText("종목 정보를 불러오지 못했습니다.", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "잘못된 종목" })).toHaveCount(0);
  allowCorrect = true;
  const beforeRetry = calls;
  await page.getByRole("button", { name: "다시 시도", exact: true }).click();
  await expect(page.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible();
  expect(calls).toBeGreaterThan(beforeRetry);
});
