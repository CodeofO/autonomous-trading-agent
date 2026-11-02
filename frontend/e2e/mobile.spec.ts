import { expect, test, type Page } from "@playwright/test";

// 휴대폰 화면(iPhone 14 크기): 가로로 넘치지 않고, 하단 탭바로 이동하며, 채팅 입력창이 탭바에 가리지 않는다
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

const noHorizontalScroll = async (page: Page) =>
  expect(Number(await page.evaluate("document.documentElement.scrollWidth"))).toBeLessThanOrEqual(390);

test("모바일: 하단 탭바로 모든 화면을 오가고 가로 스크롤이 없다", async ({ page }) => {
  await page.goto("/");
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

test("모바일: 트레이딩 룸 입력창이 탭바 위에 보인다", async ({ page }) => {
  await page.goto("/room");
  const input = page.getByRole("textbox", { name: "메시지 입력" });
  await expect(input).toBeVisible();
  const inputBox = await input.boundingBox();
  const barBox = await page.locator(".sidebar").boundingBox();
  expect(inputBox && barBox && inputBox.y + inputBox.height).toBeLessThanOrEqual(barBox!.y);
});
