import { memo, useSyncExternalStore } from "react";
import { fmtAgo, fmtFull } from "../utils/format";

/*
 * 화면의 모든 상대 시각이 함께 쓰는 시계 — 30초마다 한 번 (보는 상대 시각이 하나도 없으면 멈춘다)
 */
const TICK_MS = 30_000;
let now = Date.now();
let timer = 0;
const listeners = new Set<() => void>();

function subscribe(fn: () => void) {
  listeners.add(fn);
  if (listeners.size === 1) {
    now = Date.now();
    timer = window.setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) window.clearInterval(timer);
  };
}
const getNow = () => now;

/** "3분 전" — 마우스를 올리면(길게 누르면) 정확한 한국 시각 */
export const RelTime = memo(function RelTime({ iso, className }: { iso: string; className?: string }) {
  // 시계가 움직일 때마다 다시 그린다 (값은 그때의 지금 시각)
  const t = useSyncExternalStore(subscribe, getNow, getNow);
  return (
    <time dateTime={iso} title={fmtFull(iso)} className={className}>
      {fmtAgo(iso, Math.max(t, Date.now()))}
    </time>
  );
});
