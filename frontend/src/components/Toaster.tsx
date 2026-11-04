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

function Toast({ t }: { t: ToastItem }) {
  // 글자는 이 요소에 바로 둔다 (아이콘은 CSS) — 화면 읽기·검색에 글자 그대로 잡힌다
  return (
    <div className={`toast ${t.tone}`} data-testid="toast" onClick={() => dismissToast(t.id)}>
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
