import { useRef, type TouchEvent } from "react";
import { dismissToast, useToasts, type ToastItem } from "../ui/toast";

/**
 * 토스트를 그리는 곳 (앱에 하나). 읽기 프로그램은 성공·안내를 차례로(polite), 실패는 바로(assertive) 읽는다.
 * 누르면 닫힌다. 휴대폰은 하단 탭바 위, 넓은 화면은 오른쪽 아래
 */
export function Toaster() {
  const items = useToasts();
  const calm = items.filter((t) => t.tone !== "error");
  const errors = items.filter((t) => t.tone === "error");
  return (
    <div className="toaster" data-testid="toaster">
      <div className="toast-stack" aria-live="polite" aria-relevant="additions">
        {calm.map((t) => (
          <Toast key={t.id} t={t} />
        ))}
      </div>
      <div className="toast-stack" aria-live="assertive" aria-relevant="additions">
        {errors.map((t) => (
          <Toast key={t.id} t={t} />
        ))}
      </div>
    </div>
  );
}

/** 이만큼 옆으로 밀면 토스트를 닫는다(px) */
const TOAST_SWIPE_PX = 56;

function Toast({ t }: { t: ToastItem }) {
  // 손가락으로 옆으로 밀어 닫기 (휴대폰). 미는 동안은 손가락을 따라오고 흐려진다
  const start = useRef<number | null>(null);
  const onTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    start.current = e.touches[0].clientX;
  };
  const onTouchMove = (e: TouchEvent<HTMLDivElement>) => {
    if (start.current === null) return;
    const dx = e.touches[0].clientX - start.current;
    e.currentTarget.style.transform = `translateX(${dx}px)`;
    e.currentTarget.style.opacity = String(Math.max(0.2, 1 - Math.abs(dx) / 160));
  };
  const onTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    if (start.current === null) return;
    const dx = e.changedTouches[0].clientX - start.current;
    start.current = null;
    if (Math.abs(dx) >= TOAST_SWIPE_PX) {
      dismissToast(t.id);
      return;
    }
    e.currentTarget.style.transform = "";
    e.currentTarget.style.opacity = "";
  };
  // 글자는 이 요소에 바로 둔다 (아이콘은 CSS) — 화면 읽기·검색에 글자 그대로 잡힌다
  return (
    <div
      className={`toast ${t.tone}`}
      data-testid="toast"
      onClick={() => dismissToast(t.id)}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      {t.text}
      <button
        type="button"
        className="toast-close"
        aria-label="알림 닫기"
        onClick={(e) => {
          e.stopPropagation();
          dismissToast(t.id);
        }}
      >
        ×
      </button>
    </div>
  );
}
