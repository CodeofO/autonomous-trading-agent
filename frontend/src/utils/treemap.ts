/*
 * 모자이크(트리맵) 배치 — Squarified treemap (Bruls·Huizing·van Wijk, 2000).
 * 값이 큰 것부터 짧은 변을 따라 한 줄씩 깔되, 줄의 칸들이 정사각형에 가장 가깝게 되는 데까지만 넣는다.
 * 넓이는 값에 비례하고, 칸은 상자 안에서 겹치지 않는다. 0 이하 값은 그리지 않는다.
 */

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type Placed<T> = T & Box;

/** 한 줄(row)의 가장 나쁜 가로세로 비 — 작을수록 정사각형에 가깝다 */
function worst(areas: number[], side: number): number {
  if (areas.length === 0) return Infinity;
  let sum = 0;
  let max = -Infinity;
  let min = Infinity;
  for (const a of areas) {
    sum += a;
    if (a > max) max = a;
    if (a < min) min = a;
  }
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

/**
 * items 를 box 안에 값 비례 넓이로 배치한다. 돌려주는 순서는 값이 큰 것부터.
 * 부동소수 오차로 끝이 모자라지 않게 줄의 마지막 칸·마지막 줄은 남은 변 끝까지 채운다.
 */
export function squarify<T extends { value: number }>(items: readonly T[], box: Box): Placed<T>[] {
  const live = items.filter((it) => Number.isFinite(it.value) && it.value > 0);
  const total = live.reduce((s, it) => s + it.value, 0);
  if (total <= 0 || box.w <= 0 || box.h <= 0) return [];
  const scale = (box.w * box.h) / total;
  const sorted = [...live].sort((a, b) => b.value - a.value);
  const out: Placed<T>[] = [];
  let rect: Box = { ...box };
  let row: T[] = [];
  let rowAreas: number[] = [];

  const flush = (last: boolean) => {
    if (row.length === 0) return;
    const sum = rowAreas.reduce((s, a) => s + a, 0);
    if (rect.w >= rect.h) {
      // 세로로 한 열: 왼쪽에 폭 sum/h 만큼
      const colW = last ? rect.w : Math.min(rect.w, sum / rect.h);
      let y = rect.y;
      row.forEach((it, i) => {
        const h = i === row.length - 1 ? rect.y + rect.h - y : rowAreas[i] / colW;
        out.push({ ...it, x: rect.x, y, w: colW, h });
        y += h;
      });
      rect = { x: rect.x + colW, y: rect.y, w: rect.w - colW, h: rect.h };
    } else {
      // 가로로 한 줄: 위쪽에 높이 sum/w 만큼
      const rowH = last ? rect.h : Math.min(rect.h, sum / rect.w);
      let x = rect.x;
      row.forEach((it, i) => {
        const w = i === row.length - 1 ? rect.x + rect.w - x : rowAreas[i] / rowH;
        out.push({ ...it, x, y: rect.y, w, h: rowH });
        x += w;
      });
      rect = { x: rect.x, y: rect.y + rowH, w: rect.w, h: rect.h - rowH };
    }
    row = [];
    rowAreas = [];
  };

  sorted.forEach((it, i) => {
    const area = it.value * scale;
    const side = Math.min(rect.w, rect.h);
    if (row.length > 0 && worst([...rowAreas, area], side) > worst(rowAreas, side)) flush(false);
    row.push(it);
    rowAreas.push(area);
    if (i === sorted.length - 1) flush(true);
  });
  return out;
}
