import { expect, test } from "@playwright/test";

test("로그인 없이 대시보드 → 킬 스위치 → 재개", async ({ page }) => {
  // 처음 화면은 '전체'(두 시장 한눈에 보기) — 국장 대시보드·킬 스위치를 보려고 ?m=kr 로 연다
  await page.goto("/?m=kr");
  await expect(page.getByText("계정 만들고 시작하기")).toHaveCount(0);

  // 대시보드: PAPER 배지와 엔진 상태
  await expect(page.getByRole("status", { name: "모의투자 모드" })).toContainText("PAPER");
  await expect(page.getByTestId("engine-status")).toHaveText(/실행 중/);
  await expect(page.getByText("총자산")).toBeVisible();

  // 킬 스위치: 확인 모달을 거쳐 정지
  await page.getByRole("button", { name: "킬 스위치" }).click();
  const dialog = page.getByRole("dialog", { name: /킬 스위치를 작동할까요/ });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "즉시 전체 정지" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("engine-status")).toHaveText(/정지/);
  await expect(page.getByRole("button", { name: /지금 판단 실행/ })).toBeDisabled();

  // 트레이딩 룸에 시스템 메시지가 실시간으로 도착
  await page
    .getByRole("link", { name: /트레이딩 룸/ })
    .first()
    .click();
  await expect(page.getByText(/킬 스위치 작동/).first()).toBeVisible();

  // 재개
  await page.getByRole("button", { name: /자동매매 재개/ }).click();
  await expect(page.getByTestId("engine-status")).toHaveText(/실행 중/);
});

test("트레이딩 룸에서 판단 세션을 실행하면 결정 카드가 채팅으로 흐른다", async ({ page }) => {
  await page.goto("/room?m=kr");
  await page.getByRole("button", { name: /지금 판단 실행/ }).click();
  // 세션 하나가 한 덩어리(헤더·결정·확인한 정보)로 묶여 흐른다 (판단 세션 정리)
  const session = page.getByTestId("session").first();
  await expect(session).toBeVisible({ timeout: 20_000 });
  await expect(session.getByTestId("session-status")).toHaveText(/완료/, { timeout: 30_000 });
  await expect(session.getByText(/규칙 점검 끝/).first()).toBeVisible({ timeout: 30_000 });
});
