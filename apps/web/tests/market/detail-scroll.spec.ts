import { test, expect } from "@playwright/test";

for (const width of [390, 900, 1440]) {
  test(`security header scrolls out without covering charts at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stocks?code=005930");
    const header = page.locator(".mk-security-header");
    await expect(header.getByRole("heading", { name: "삼성전자", exact: true })).toBeVisible({timeout:20000});
    const before = await header.boundingBox();
    // Visibility alone accepts a button hidden behind the fixed site header.
    // Its containing block must remain the security summary after sticky removal.
    const favorite = header.getByRole("button", { name: /관심종목/ });
    await expect(favorite).toBeVisible();
    const action = await favorite.boundingBox();
    expect(action!.y).toBeGreaterThanOrEqual(before!.y);
    expect(action!.y + action!.height).toBeLessThanOrEqual(before!.y + before!.height + 1);
    expect(await favorite.evaluate(node => {
      const box = node.getBoundingClientRect();
      return node.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    })).toBe(true);
    await page.evaluate(() => {
      const next = Math.min(650, document.documentElement.scrollHeight - innerHeight);
      window.scrollTo({top: next, behavior: "instant"});
      return next;
    });
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await expect.poll(async () => (await header.boundingBox())!.y).toBeLessThan(0);
    // Late chart data can move the scroll anchor; compare the real offset rather
    // than the requested scroll position so a sticky header still fails.
    const position = await header.evaluate(node => ({top:node.getBoundingClientRect().top,scroll:window.scrollY}));
    expect(before!.y - position.top).toBeCloseTo(position.scroll, 0);
    expect(await header.evaluate(node => getComputedStyle(node).position)).not.toBe("sticky");
  });
}
