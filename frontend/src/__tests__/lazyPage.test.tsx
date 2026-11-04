import { act, render, screen } from "@testing-library/react";
import { Suspense, useState } from "react";
import { describe, expect, it } from "vitest";
import { lazyWithPreload } from "../lazyPage";

function makePage(label: string) {
  let mounts = 0;
  function Inner() {
    const [id] = useState(() => ++mounts);
    return <p>{`${label} #${id}`}</p>;
  }
  const Page = lazyWithPreload(
    () => Promise.resolve({ Inner }),
    (m) => m.Inner,
  );
  return { Page, mounts: () => mounts };
}

describe("lazyWithPreload — 미리 받은 화면은 멈추지 않고 바로 그린다", () => {
  it("preload 가 끝난 화면은 첫 렌더에서 대기 화면 없이 바로 나온다", async () => {
    const { Page } = makePage("전체");
    await Page.preload();
    render(
      <Suspense fallback={<p>로딩</p>}>
        <Page />
      </Suspense>,
    );
    expect(screen.getByText("전체 #1")).toBeInTheDocument(); // 동기적으로 이미 그려져 있다
    expect(screen.queryByText("로딩")).not.toBeInTheDocument();
  });

  it("안 받은 화면은 지금처럼 대기 화면 뒤에 나오고, 떠 있는 동안 다시 만들어지지 않는다", async () => {
    const { Page, mounts } = makePage("대시보드");
    const { rerender } = render(
      <Suspense fallback={<p>로딩</p>}>
        <Page />
      </Suspense>,
    );
    expect(screen.getByText("로딩")).toBeInTheDocument();
    expect(await screen.findByText("대시보드 #1")).toBeInTheDocument();
    await act(async () => {
      rerender(
        <Suspense fallback={<p>로딩</p>}>
          <Page />
        </Suspense>,
      );
    });
    expect(screen.getByText("대시보드 #1")).toBeInTheDocument(); // 같은 인스턴스 유지(상태 보존)
    expect(mounts()).toBe(1);
  });
});
