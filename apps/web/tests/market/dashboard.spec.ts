import { test, expect } from "@playwright/test";
test("real-source search, saved view, detail, help, comparison and return", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/stocks");
  await expect(
    page.getByRole("heading", { name: "오늘의 시장" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "삼성전자 005930 KOSPI", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "시가총액", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /시가총액 상위.*전체 [\d,]+개/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "거래대금", exact: true }).click();
  await page
    .getByRole("button", { name: "삼성전자 관심종목 추가", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "주 메뉴", exact: true })
    .getByRole("button", { name: "종목 찾기", exact: true })
    .click();
  await page.getByRole("button", { name: "필터", exact: true }).click();
  await page
    .getByRole("button", { name: "조건 추가", exact: true })
    .first()
    .click();
  await page.getByRole("spinbutton", { name: "rules 값 1" }).fill("30");
  await page.getByRole("button", { name: "조건 적용", exact: true }).click();
  await page.getByRole("button", { name: "보기 저장", exact: true }).click();
  await page.getByLabel("보기 이름").fill("검수 보기");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "보기 저장", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "저장한 보기" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "전체 초기화", exact: true }).click();
  await page
    .getByRole("button", { name: "삼성전자 005930 KOSPI", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "삼성전자", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "재무·지표" }).click();
  await expect(page.locator(".mk-financial tbody tr")).not.toHaveCount(0);
  await page.getByRole("tab", { name: "요약", exact: true }).click();
  await page
    .getByRole("button", { name: "외국인 지분율", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("계산 방식");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "외국인 지분율", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "목록으로 돌아가기" }).click();
  await page
    .getByRole("checkbox", { name: "삼성전자 비교 선택", exact: true })
    .check();
  await page
    .getByRole("checkbox", { name: "SK하이닉스 비교 선택", exact: true })
    .check();
  await page.getByRole("button", { name: "종목 비교", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "나란히 비교하기" }),
  ).toBeVisible();
  await expect(page.locator(".mk-compare")).toContainText("삼성전자");
  await expect(page.locator(".mk-compare")).toContainText("SK하이닉스");
  await page.reload();
  await expect(page.locator(".mk-compare")).toContainText("삼성전자");
  expect(errors).toEqual([]);
});

for (const width of [360, 390, 768, 1440])
  test(`responsive ${width}px, light and dark`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/stocks");
    await expect(
      page.getByRole("button", { name: "삼성전자 005930 KOSPI", exact: true }),
    ).toBeVisible();
    for (const theme of ["라이트", "다크"]) {
      await page
        .getByRole("button", { name: "화면 설정", exact: true })
        .click();
      await page.getByRole("button", { name: theme, exact: true }).click();
      await page.getByRole("button", { name: "닫기", exact: true }).click();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `../../docs/screenshots/market-${width}-${theme === "라이트" ? "light" : "dark"}.png`,
        fullPage: true,
      });
    }
    await page.getByRole("button", { name: "화면 설정", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: /^글자 크기/ }).click();
    await page.getByRole("button", { name: "더 크게", exact: true }).click();
    await page.getByRole("button", { name: "이 크기로 보기", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
  });

test("filter cancel, search empty and stable row order", async ({ page }) => {
  await page.goto("/stocks?page=screener");
  await expect(page.locator(".mk-stock-table tbody tr")).not.toHaveCount(0);
  const before = await page.locator(".mk-stock-link").allTextContents();
  await page.getByRole("button", { name: "필터", exact: true }).click();
  await page
    .getByRole("button", { name: "조건 추가", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await expect(page.locator(".mk-filter-chips")).toHaveCount(0);
  expect(await page.locator(".mk-stock-link").allTextContents()).toEqual(
    before,
  );
  const searchRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/market/v1/search"))
      searchRequests.push(request.url());
  });
  await page
    .getByRole("button", { name: "종목명 또는 코드 검색 Ctrl K" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("거래대금 상위 종목");
  await expect(page.locator(".mk-search-result .mk-company-logo").first()).toBeVisible();
  await page.waitForTimeout(250);
  expect(searchRequests).toHaveLength(0);
  await page.screenshot({
    path: "../../docs/ux-audit/run-20260925-improvement/05-search-instant.png",
  });
  await page
    .getByRole("textbox", { name: "종목명 또는 코드", exact: true })
    .fill("존재하지않는종목abcdef");
  await expect(page.getByRole("dialog")).toContainText("검색 결과가 없습니다.",{timeout:15000});
});

test("mobile search suggestions stay usable without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stocks");
  await expect(
    page.getByRole("button", { name: "삼성전자 005930 KOSPI", exact: true }),
  ).toBeVisible();
  const searchRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/market/v1/search"))
      searchRequests.push(request.url());
  });
  await page.getByRole("button", { name: "종목명 또는 코드 검색 Ctrl K" }).click();
  await expect(page.getByRole("dialog")).toContainText("거래대금 상위 종목");
  await expect(page.locator(".mk-search-result .mk-company-logo").first()).toBeVisible();
  await page.waitForTimeout(250);
  expect(searchRequests).toHaveLength(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../docs/ux-audit/run-20260925-improvement/06-search-mobile.png",
  });
});

test('mobile back closes the panel before leaving the page; chart controls work',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/stocks');
 await page.getByRole('button',{name:'화면 설정',exact:true}).click();
 await expect(page.getByRole('dialog')).toBeVisible();
 await page.goBack();await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'오늘의 시장'})).toBeVisible();
 await page.getByRole('button',{name:'삼성전자 005930 KOSPI',exact:true}).click();
 await page.getByRole('tab',{name:'차트',exact:true}).click();
 await page.getByRole('button',{name:'장기 · 거래량',exact:true}).click();
 await page.getByRole('combobox',{name:'차트 봉 선택'}).selectOption('week');
 await page.getByRole('button',{name:'캔들 보기',exact:true}).click();
 await expect(page.getByRole('heading',{name:'주가와 실적 함께 보기'})).toBeVisible();
 await page.screenshot({path:'../../docs/screenshots/detail-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'목록으로 돌아가기'}).click();
 await expect(page.getByRole('button',{name:'삼성전자 005930 KOSPI',exact:true})).toBeFocused();
});

test('stock detail explains that its history chart is still preparing', async ({ page }) => {
  await page.route('**/api/market/v1/securities/000660/prices', route => route.fulfill({json:{rows:[],status:'collecting'}}));
  await page.route(/\/api\/market\/v1\/securities\/000660(?:\?.*)?$/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const payload = await response.json();
    payload.prices = null;
    payload.collection ??= {};
    payload.collection.sources ??= {};
    payload.collection.sources.history = { status: "pending" };
    await route.fulfill({
      response,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
  await page.goto("/stocks");
  await expect(page.getByText(/출처|갱신 시각|원천 한도|제공 한도/)).toHaveCount(0);
  await page.getByRole("button", { name: "종목명 또는 코드 검색 Ctrl K" }).click();
  await page.getByRole("textbox", { name: "종목명 또는 코드" }).fill("000660");
  await expect(page.getByRole("button", { name: /SK하이닉스.*KOSPI.*000660/ })).toBeVisible();
  await page.getByRole("button", { name: /SK하이닉스.*KOSPI.*000660/ }).click();
  await page.getByRole("button", { name: "장기 · 거래량", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("차트 준비 중");
  await expect(page.getByRole("status")).toContainText("잠시 후 자동으로 나타납니다");
  await expect(page.getByText(/출처|갱신 시각|원천 한도|제공 한도/)).toHaveCount(0);
});

test('malformed shared filters are normalized and offline recovery is explicit',async({page,context})=>{
 await page.goto('/stocks?page=screener&filters='+encodeURIComponent(JSON.stringify({rules:null,any_rules:3,sort:'bad',q:[]})));
 await expect(page.locator('.mk-stock-table tbody tr')).not.toHaveCount(0);
 await context.setOffline(true);await expect(page.getByRole('status').filter({hasText:'연결이 끊겼습니다'})).toBeVisible();
 await context.setOffline(false);await expect(page.locator('.mk-connection')).toHaveCount(0);
});
