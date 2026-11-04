import { expect, test, type Page } from "@playwright/test";

// 휴대폰 화면(iPhone 14 크기): 가로로 넘치지 않고, 하단 탭바로 이동하며, 채팅 입력창이 탭바에 가리지 않는다
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

const noHorizontalScroll = async (page: Page) =>
  expect(Number(await page.evaluate("document.documentElement.scrollWidth"))).toBeLessThanOrEqual(390);

test("모바일: 하단 탭바로 모든 화면을 오가고 가로 스크롤이 없다", async ({ page }) => {
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
    ["에이전트 구조", "/agent"],
    ["포트폴리오·주문", "/portfolio"],
    ["백테스트", "/backtest"],
    ["설정", "/settings"],
    ["이벤트 로그", "/logs"],
    ["대시보드", "/"],
  ]) {
    await tabbar.getByRole("link", { name }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await noHorizontalScroll(page);
  }
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

test("모바일: 본문만 스크롤하고, 탭마다 스크롤 위치를 기억한다", async ({ page }) => {
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

  // 다른 탭은 맨 위에서 시작 → 돌아오면 아까 위치
  await tabbar.getByRole("link", { name: "설정" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect.poll(() => scrollTop(page)).toBe(0);
  await expect(page.locator(".topbar")).not.toHaveClass(/scrolled/);
  await tabbar.getByRole("link", { name: "에이전트 구조" }).click();
  await expect(page).toHaveURL(/\/agent$/);
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(kept - 4);
  await noHorizontalScroll(page);

  // 선택 표시가 탭 위치를 따라가고, 같은 탭을 다시 누르면 맨 위로
  await expect(tabbar.locator("nav")).toHaveAttribute("style", /--tab-i: ?2/);
  await tabbar.getByRole("link", { name: "에이전트 구조" }).click();
  await expect.poll(() => scrollTop(page)).toBe(0);
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
    await page.locator(".sidebar").getByRole("link", { name: "설정" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator("html")).not.toHaveClass(/vt-nav/);
  });
});
