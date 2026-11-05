import { useCallback, useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { motionOk } from "../mobile";
import "./Sheet.css";

/** 시트를 닫는 데 걸리는 시간 (styles.css .sheet.closing 과 같게) */
export const SHEET_CLOSE_MS = 180;
/** 이만큼 끌어내리면 닫는다(px) */
const DRAG_CLOSE_PX = 90;
/** 열려 있는 시트 수 (겹쳐 열려도 마지막이 닫힐 때 배경 스크롤을 푼다) */
let openSheets = 0;

interface Props {
  /** 화면 읽기용 이름 (title 이 없을 때도 꼭) */
  label: string;
  title?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  /** 아래에 붙는 버튼 줄 (확인 창) */
  actions?: ReactNode;
  tone?: "danger" | "default";
  className?: string;
  /** wide: 넓은 화면에서는 가운데 큰 창(여러 차트를 한 번에), 휴대폰에서는 화면 높이 가득 */
  size?: "default" | "wide";
}

/**
 * 아래에서 올라오는 시트 (휴대폰). 확인 창·종목 빠른 보기에 쓴다.
 * - 손잡이·머리줄을 끌어내리거나, 바깥(어두운 곳)을 누르거나, Esc 로 닫는다
 * - 열리면 시트 안으로 초점을 옮기고, 닫히면 열기 전 자리로 돌려준다
 * - 홈 표시줄(safe-area)만큼 아래 여백을 둔다. '동작 줄이기'면 미끄러지지 않고 바로 나타난다
 */
export function Sheet({
  label,
  title,
  children,
  onClose,
  actions,
  tone = "default",
  className = "",
  size = "default",
}: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const [closing, setClosing] = useState(false);
  const [drag, setDrag] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ y: number; t: number } | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  const close = useCallback(() => {
    if (!motionOk()) {
      onCloseRef.current();
      return;
    }
    setClosing(true);
    window.setTimeout(() => onCloseRef.current(), SHEET_CLOSE_MS);
  }, []);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const root = document.documentElement;
    openSheets++;
    root.classList.add("sheet-open");
    // 첫 버튼(없으면 시트 자체)으로 초점
    const first = panel.current?.querySelector<HTMLElement>(
      "button:not([disabled]), a[href], input, select, textarea",
    );
    (first ?? panel.current)?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key !== "Tab" || !panel.current) return;
      // 초점이 시트 밖으로 나가지 않게 (앞뒤로 돈다)
      const items = panel.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), a[href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])",
      );
      if (items.length === 0) return;
      const a = items[0];
      const z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        z.focus();
      } else if (!e.shiftKey && document.activeElement === z) {
        e.preventDefault();
        a.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      // 다른 시트가 아직 열려 있으면 표시를 남긴다
      openSheets = Math.max(0, openSheets - 1);
      if (openSheets === 0) root.classList.remove("sheet-open");
      before?.focus?.({ preventScroll: true });
    };
  }, [close]);

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    start.current = { y: e.clientY, t: performance.now() };
    setDragging(true);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    // 위로는 조금만(고무줄), 아래로는 손가락을 따라
    const dy = e.clientY - start.current.y;
    setDrag(dy > 0 ? dy : dy / 4);
  };
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    const s = start.current;
    start.current = null;
    setDragging(false);
    if (!s) return;
    const dy = e.clientY - s.y;
    const speed = dy / Math.max(1, performance.now() - s.t); // px/ms
    if (dy > DRAG_CLOSE_PX || (dy > 24 && speed > 0.6)) close();
    else setDrag(0);
  };

  return createPortal(
    <div className={`sheet-backdrop ${size} ${closing ? "closing" : ""}`.trim()} onClick={close}>
      <div
        ref={panel}
        className={`sheet ${tone} ${size === "wide" ? "wide" : ""} ${closing ? "closing" : ""} ${dragging && drag ? "dragging" : ""} ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        style={drag ? { transform: `translateY(${Math.round(drag)}px)` } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="sheet-grab"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
        >
          <span className="sheet-handle" aria-hidden />
          {title && <h3 className="sheet-title">{title}</h3>}
        </div>
        <div className="sheet-body">{children}</div>
        {actions && <div className="sheet-actions">{actions}</div>}
      </div>
    </div>,
    document.body,
  );
}
