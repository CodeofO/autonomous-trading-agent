/// <reference lib="dom" />
// page.evaluate 안의 코드는 브라우저에서 돈다 (터치 이벤트를 만들려고 DOM 타입을 쓴다)
import { expect, test, type Page } from "@playwright/test";

// 휴대폰 화면(iPhone 14 크기): 가로로 넘치지 않고, 하단 탭바로 이동하며, 채팅 입력창이 탭바에 가리지 않는다
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

const noHorizontalScroll = async (page: Page) =>
  expect(Number(await page.evaluate("document.documentElement.scrollWidth"))).toBeLessThanOrEqual(390);

test("모바일: 하단 탭바(4칸 + 더보기)와 오른쪽 위 설정으로 모든 화면을 오가고 가로 스크롤이 없다", async ({
  page,
}) => {
  // 국장 대시보드부터 (처음 화면 '전체'는 두 시장 한눈에 보기)
  await page.goto("/?m=kr");
  await expect(page.getByText("총자산")).toBeVisible();
  await noHorizontalScroll(page);

  const tabbar = page.locator(".sidebar");
  const box = await tabbar.boundingBox();
  expect(box && box.y + box.height).toBeGreaterThan(800); // 화면 맨 아래에 붙어 있다
  await expect(page.getByRole("button", { name: "킬 스위치" })).toBeVisible();

  for (const [name, path] of [
    ["트레이딩 룸", "/room"],
    ["포트폴리오·주문", "/portfolio"],
    ["뉴스 룸", "/news"],
    ["홈", "/"],
  ]) {
    await tabbar.getByRole("link", { name }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await noHorizontalScroll(page);
  }
  // 탭바에 없는 화면은 '더보기' 시트에서
  for (const [name, path] of [
    [/^매니저/, "/agent"],
    [/^백테스트/, "/backtest"],
    [/^판단 일정/, "/settings/schedule"],
    [/^이벤트 로그/, "/logs"],
    [/^LLM 요금/, "/costs"],
  ] as const) {
    await tabbar.getByRole("button", { name: "더보기" }).click();
    await page.getByRole("dialog", { name: "더보기" }).getByRole("link", { name }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole("dialog", { name: "더보기" })).toBeHidden();
    await noHorizontalScroll(page);
  }
  // 설정은 오른쪽 위 ⚙️
  await page.locator(".topbar").getByRole("link", { name: "설정", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await noHorizontalScroll(page);
});

test("모바일: 트레이딩 룸 입력창이 탭바 위에 보이고, 입력 중에는 탭바가 비켜 준다", async ({ page }) => {
  await page.goto("/room");
  const input = page.getByRole("textbox", { name: "메시지 입력" });
  await expect(input).toBeVisible();
  const inputBox = await input.boundingBox();
  const barBox = await page.locator(".sidebar").boundingBox();
  expect(inputBox && barBox && inputBox.y + inputBox.height).toBeLessThanOrEqual(barBox!.y);
  // 채팅 카드가 상단 바와 탭바 사이를 꽉 채운다 (입력창이 탭바 바로 위)
  expect(barBox!.y - (inputBox!.y + inputBox!.height)).toBeLessThan(48);

  await input.focus();
  await expect(page.locator("html")).toHaveClass(/kb-open/);
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(input).toBeInViewport();
  await input.blur();
  await expect(page.locator(".sidebar")).toBeVisible();
});

const scroller = (page: Page) => page.getByTestId("page-scroll");
const scrollTop = (page: Page) => scroller(page).evaluate((el) => Math.round(el.scrollTop));

test("모바일: 본문만 스크롤하고, 화면마다 스크롤 위치를 기억한다", async ({ page }) => {
  await page.goto("/agent");
  const tabbar = page.locator(".sidebar");
  await expect(page.locator(".agent-page")).toBeVisible();
  await expect(scroller(page)).toHaveCSS("overflow-y", "auto");

  // 손가락으로 내리듯 휠을 굴려도 문서(body)는 그대로, 본문만 움직인다
  await page.mouse.move(195, 400);
  await page.mouse.wheel(0, 600);
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(300);
  expect(Number(await page.evaluate("document.scrollingElement.scrollTop"))).toBe(0);
  await expect(page.locator(".topbar")).toHaveClass(/scrolled/);
  const kept = await scrollTop(page);

  // 다른 화면은 맨 위에서 시작 → 돌아오면 아까 위치
  await page.locator(".topbar").getByRole("link", { name: "설정", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect.poll(() => scrollTop(page)).toBe(0);
  await expect(page.locator(".topbar")).not.toHaveClass(/scrolled/);
  await tabbar.getByRole("button", { name: "더보기" }).click();
  await page
    .getByRole("dialog", { name: "더보기" })
    .getByRole("link", { name: /^매니저/ })
    .click();
  await expect(page).toHaveURL(/\/agent$/);
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(kept - 4);
  await noHorizontalScroll(page);
  // 매니저는 '더보기' 아래 화면 — 선택 표시가 마지막 칸(더보기)에
  await expect(tabbar.locator("nav")).toHaveAttribute("style", /--tab-i: ?4/);

  // 탭을 누르면 그 탭으로
  await tabbar.getByRole("link", { name: "홈" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(tabbar.locator("nav")).toHaveAttribute("style", /--tab-i: ?0/);

  // 보고 있는 탭을 다시 누르면 맨 위로 (내용이 긴 국장 대시보드에서)
  await page.goto("/?m=kr");
  await expect(page.getByText("총자산")).toBeVisible();
  await page.mouse.move(195, 400);
  await page.mouse.wheel(0, 600);
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(100);
  await tabbar.getByRole("link", { name: "홈" }).click();
  await expect.poll(() => scrollTop(page)).toBe(0);
});

/** 본문을 손가락으로 옆으로 넘긴다 (실제 터치 이벤트) */
const swipeContent = (page: Page, dx: number) =>
  page.evaluate((dx) => {
    const el = document.querySelector<HTMLElement>('[data-testid="page-scroll"]')!;
    const at = (x: number) => new Touch({ identifier: 1, target: el, clientX: x, clientY: 420 });
    const fire = (type: string, x: number, active: boolean) =>
      el.dispatchEvent(
        new TouchEvent(type, {
          touches: active ? [at(x)] : [],
          changedTouches: [at(x)],
          bubbles: true,
          cancelable: true,
        }),
      );
    fire("touchstart", 190, true);
    fire("touchmove", 190 + dx / 2, true);
    fire("touchend", 190 + dx, false);
  }, dx);

test("모바일: 옆으로 넘기면 옆 탭으로, 내려도 상단 바 높이는 그대로", async ({ page }) => {
  await page.goto("/?m=kr");
  await expect(page.getByText("총자산")).toBeVisible();
  // 상단 바가 접혔다 펴지며 본문이 튀지 않게 — 스크롤 전후 본문(스크롤 상자) 위치·높이가 같다
  const before = await scroller(page).boundingBox();
  await page.mouse.move(195, 500);
  await page.mouse.wheel(0, 500);
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(200);
  await page.waitForTimeout(300);
  expect(await scroller(page).boundingBox()).toEqual(before);
  await expect(page.getByRole("group", { name: "시장 보기" })).toBeVisible();
  await page.mouse.wheel(0, -500);

  await swipeContent(page, -140);
  await expect(page).toHaveURL(/\/room$/);
  await swipeContent(page, -140);
  await expect(page).toHaveURL(/\/portfolio$/);
  await swipeContent(page, 140);
  await expect(page).toHaveURL(/\/room$/);
  await noHorizontalScroll(page);
});

test.describe("넓은 화면", () => {
  test.use({ viewport: { width: 1440, height: 900 }, hasTouch: false });

  test("데스크톱 레이아웃은 그대로: 왼쪽 사이드바, 본문(.content)이 스크롤", async ({ page }) => {
    await page.goto("/agent");
    await expect(page.locator(".agent-page")).toBeVisible();
    const side = await page.locator(".sidebar").boundingBox();
    expect(side).toMatchObject({ x: 0, y: 0, width: 220 });
    await expect(page.locator(".content")).toHaveCSS("overflow-y", "auto");
    await expect(scroller(page)).toHaveCSS("display", "contents");
    await expect(page.locator(".tab-ind")).toBeHidden();
    // 사이드바는 두 묶음, 설정은 오른쪽 위
    await expect(page.locator(".sidebar").getByRole("group", { name: "매일 보기" })).toBeVisible();
    await expect(page.locator(".sidebar").getByRole("link", { name: "설정", exact: true })).toHaveCount(0);
    await page.locator(".topbar").getByRole("link", { name: "설정", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator("html")).not.toHaveClass(/vt-nav/);
  });
});
