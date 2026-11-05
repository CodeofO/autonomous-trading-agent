import { afterEach, describe, expect, it } from "vitest";
import { currentEntry, entryScript, isBusy, makeVersionCheck, mayReload } from "../version";

const html = (entry: string) =>
  `<!doctype html><html><head><script type="module" crossorigin src="${entry}"></script>` +
  `<link rel="modulepreload" crossorigin href="/assets/vendor-react-abc.js"></head><body></body></html>`;

describe("재배포 감지 → 자동 새로고침", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    document.head.innerHTML = "";
  });

  it("index.html 에서 입구 스크립트 주소를 꺼낸다", () => {
    expect(entryScript(html("/assets/index-AAA.js"))).toBe("/assets/index-AAA.js");
    expect(entryScript("<html><body>no script</body></html>")).toBeNull();
    document.head.innerHTML = '<script type="module" src="/assets/index-NOW.js"></script>';
    expect(currentEntry()).toBe("/assets/index-NOW.js");
  });

  it("화면 입구 스크립트가 바뀌면(새 화면 배포) 바뀐 것", async () => {
    let entry = "/assets/index-AAA.js";
    const check = makeVersionCheck(
      { index: async () => html(entry), boot: async () => "b1" },
      "/assets/index-AAA.js",
    );
    expect(await check()).toBe(false);
    entry = "/assets/index-BBB.js";
    expect(await check()).toBe(true);
  });

  it("서버 boot 가 처음 본 값과 달라지면(서버 재배포) 바뀐 것, 재시작 중(못 받음)은 바뀐 것으로 보지 않는다", async () => {
    let boot: string | null = "b1";
    const check = makeVersionCheck(
      { index: async () => null, boot: async () => boot },
      "/assets/index-AAA.js",
    );
    expect(await check()).toBe(false); // 처음 본 값을 기억
    boot = null; // 서버가 다시 뜨는 중 (502)
    expect(await check()).toBe(false);
    boot = "b2";
    expect(await check()).toBe(true);
  });

  it("받다가 실패해도 바뀐 것으로 보지 않는다", async () => {
    const check = makeVersionCheck(
      {
        index: () => Promise.reject(new Error("offline")),
        boot: () => Promise.reject(new Error("offline")),
      },
      "/assets/index-AAA.js",
    );
    expect(await check()).toBe(false);
  });

  it("개발 서버처럼 지금 입구를 모르면 화면 비교는 하지 않는다", async () => {
    const check = makeVersionCheck(
      { index: async () => html("/assets/index-BBB.js"), boot: async () => null },
      null,
    );
    expect(await check()).toBe(false);
  });

  it("글자가 든 입력 칸에 초점이 있거나 창이 열려 있으면 기다린다", () => {
    document.body.innerHTML = '<textarea id="t"></textarea><input id="i" />';
    const t = document.getElementById("t") as HTMLTextAreaElement;
    t.focus();
    expect(isBusy()).toBe(false); // 빈 칸은 괜찮다
    t.value = "카카오 어때?";
    expect(isBusy()).toBe(true);
    t.blur();
    expect(isBusy()).toBe(false);
    document.body.insertAdjacentHTML("beforeend", '<div role="dialog">확인</div>');
    expect(isBusy()).toBe(true);
  });

  it("1분 안에 다시 새로고침하지 않는다 (무한 새로고침 방지)", () => {
    const store = new Map<string, string>();
    const s = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
    };
    expect(mayReload(s, 1_000_000)).toBe(true);
    expect(mayReload(s, 1_030_000)).toBe(false);
    expect(mayReload(s, 1_061_000)).toBe(true);
    expect(mayReload(null, 5_000_000)).toBe(false); // 저장소를 못 쓰면 하지 않는다
  });
});
