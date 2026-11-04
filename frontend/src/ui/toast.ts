import { useSyncExternalStore } from "react";

/*
 * 토스트 알림 — 저장·전환·정지·재개처럼 버튼을 누른 결과를 화면 아래에 잠깐 띄운다.
 * 어디서든 toast("저장했어요 ✓") 로 부르고, 화면에는 <Toaster />(App 맨 위) 가 그린다.
 * 오류의 자세한 내용은 지금처럼 그 자리에 남기고, 토스트는 '됐어요'를 알리는 데 쓴다.
 */

export type ToastTone = "ok" | "info" | "error";

export interface ToastItem {
  id: number;
  text: string;
  tone: ToastTone;
}

/** 한꺼번에 보이는 최대 개수 (넘치면 오래된 것부터 닫는다) */
const MAX = 3;
const DEFAULT_MS = 3800;

let items: readonly ToastItem[] = [];
let seq = 0;
const timers = new Map<number, number>();
const listeners = new Set<() => void>();

const emit = () => listeners.forEach((fn) => fn());

export function dismissToast(id: number) {
  const t = timers.get(id);
  if (t !== undefined) window.clearTimeout(t);
  timers.delete(id);
  if (!items.some((x) => x.id === id)) return;
  items = items.filter((x) => x.id !== id);
  emit();
}

function push(text: string, tone: ToastTone, ms = DEFAULT_MS): number {
  const id = ++seq;
  // 같은 글자가 이미 떠 있으면 새로 쌓지 않고 그것을 다시 띄운다 (연타)
  const same = items.find((x) => x.text === text && x.tone === tone);
  if (same) dismissToast(same.id);
  items = [...items, { id, text, tone }].slice(-MAX);
  for (const id of timers.keys()) if (!items.some((x) => x.id === id)) dismissToast(id);
  timers.set(
    id,
    window.setTimeout(() => dismissToast(id), tone === "error" ? ms + 2000 : ms),
  );
  emit();
  return id;
}

/** 성공 알림 (기본) */
export const toast = Object.assign((text: string, ms?: number) => push(text, "ok", ms), {
  ok: (text: string, ms?: number) => push(text, "ok", ms),
  info: (text: string, ms?: number) => push(text, "info", ms),
  /** 그 자리에 오류를 보여 줄 곳이 없는 동작(목록의 삭제 버튼 등)의 실패 */
  error: (text: string, ms?: number) => push(text, "error", ms),
});

/** 모두 닫는다 (로그아웃·테스트 사이) */
export function clearToasts() {
  for (const t of timers.values()) window.clearTimeout(t);
  timers.clear();
  if (items.length === 0) return;
  items = [];
  emit();
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
const snapshot = () => items;

export const useToasts = () => useSyncExternalStore(subscribe, snapshot, snapshot);
