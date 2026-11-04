import { memo, useLayoutEffect, useRef } from "react";
import { motionOk } from "../mobile";

/** 숫자가 굴러가는 시간 (짧게 — 시세처럼 자주 바뀌어도 따라잡을 수 있게) */
export const COUNT_MS = 420;

interface Props {
  value: number | null | undefined;
  format: (v: number | null | undefined) => string;
  className?: string;
  /** 오르면 빨강·내리면 파랑으로 잠깐 깜빡인다 (한국 시장 관례). 기본 켬 */
  flash?: boolean;
  /** 화면 읽기용 이름 (없으면 글자 그대로) */
  label?: string;
}

const ease = (k: number) => 1 - (1 - k) ** 3;

/**
 * 살아 있는 숫자: 값이 바뀌면 짧게 굴러가며(카운트업) 바뀌고, 오르내림을 잠깐 색으로 보여 준다.
 *
 * - React 는 마지막 값의 글자만 그린다. 굴러가는 중간 글자는 같은 글자 노드에 직접 써서 다시 그리기가 없다
 * - 처음 값(받기 전 → 받음)은 굴리지 않는다. 같은 값으로 다시 받아도 아무 일 없음
 * - '동작 줄이기'를 켰으면 굴리지도 깜빡이지도 않고 바로 바꾼다
 */
export const AnimatedNumber = memo(function AnimatedNumber({
  value,
  format,
  className = "",
  flash = true,
  label,
}: Props) {
  const el = useRef<HTMLSpanElement>(null);
  const shown = useRef(value); // 지금 화면에 보이는 값 (굴러가는 중이면 중간 값)
  const fmt = useRef(format);
  useLayoutEffect(() => {
    fmt.current = format;
  });

  // 그리기 전에 시작 글자로 되돌려 둔다 — 새 값이 한 번 번쩍 보였다가 굴러가지 않게
  useLayoutEffect(() => {
    const node = el.current;
    const from = shown.current;
    const to = value;
    if (
      !node ||
      from == null ||
      to == null ||
      !Number.isFinite(from) ||
      !Number.isFinite(to) ||
      from === to
    ) {
      shown.current = to;
      return;
    }
    if (!motionOk()) {
      shown.current = to;
      return;
    }
    if (flash) {
      node.classList.remove("flash-up", "flash-down");
      void node.offsetWidth; // 같은 방향으로 연달아 바뀌어도 깜빡임을 처음부터 다시
      node.classList.add(to > from ? "flash-up" : "flash-down");
    }
    const start = performance.now();
    let frame = 0;
    const write = (v: number) => {
      shown.current = v;
      const text = fmt.current(v);
      if (node.textContent !== text) node.textContent = text;
    };
    write(from);
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / COUNT_MS);
      if (k >= 1) {
        write(to);
        return;
      }
      write(from + (to - from) * ease(k));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      // 중간에 새 값이 오면 지금 보이는 값에서 이어서 굴린다 — 글자는 React 가 새 값으로 이미 바꿨다
    };
  }, [value, flash]);

  return (
    <span
      ref={el}
      className={`num ${className}`.trim()}
      aria-label={label}
      onAnimationEnd={(e) => e.currentTarget.classList.remove("flash-up", "flash-down")}
    >
      {format(value)}
    </span>
  );
});
