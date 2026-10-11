import { test, expect } from "@playwright/test";

test("mobile detail revalidates without a body and recovers after a failed refresh", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const source = await request.get("/api/market/v1/securities/005930");
  expect(source.ok()).toBeTruthy();
  const fixture = await source.json();
  const validators: (string | undefined)[] = [];
  let round = 0, phase = 1;
  await page.route(/\/api\/market\/v1\/securities\/005930(?:\?.*)?$/, async route => {
    validators.push(route.request().headers()["if-none-match"]);
    round++;
    if (phase === 2) {
      await route.fulfill({ status: 304, headers: { ETag: 'W/"fixture-1"' } });
    } else if (phase === 3) {
      await route.fulfill({ status: 503, json: { detail: "fixture outage" } });
    } else {
      await route.fulfill({
        status: 200,
        headers: { ETag: `W/"fixture-${phase}"` },
        json: phase === 1 ? fixture : { ...fixture, security: { ...fixture.security, name: "복구 확인 종목" } },
      });
    }
  });
  await page.goto("/stocks?code=005930");
  await expect(page.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible({timeout:20000});
  const refresh = async () => {
    const previous = round;
    phase++;
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect.poll(() => round).toBe(previous + 1);
  };
  await refresh();
  await expect(page.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible();
  await refresh();
  await expect(page.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible();
  // Ensure the failed fetch has settled before asking it to recover.
  await expect(page.getByText(/기존 화면은 계속 볼 수 있습니다/)).toBeVisible();
  await refresh();
  await expect(page.getByRole("heading", { name: "복구 확인 종목", exact: true })).toBeVisible();
  expect(validators.slice(-3)).toEqual(['W/"fixture-1"', 'W/"fixture-1"', 'W/"fixture-1"']);
});
