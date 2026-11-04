import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { useIsMobile } from "../mobile";

// 아래 시트는 휴대폰에서 처음 열 때 받는다 (킬 스위치에 손가락이 닿으면 미리 — ui/overlays.ts preloadSheet)
const Sheet = lazy(() => import("./Sheet").then((m) => ({ default: m.Sheet })));

interface Props {
  title: string;
  children: ReactNode;
  onClose: () => void;
  actions: ReactNode;
  tone?: "danger" | "default";
}

/** 확인 창. 넓은 화면은 가운데 창, 휴대폰은 아래에서 올라오는 시트 (엄지가 닿는 곳에 버튼) */
export function Modal(props: Props) {
  const mobile = useIsMobile();
  if (mobile)
    return (
      <Suspense fallback={null}>
        <Sheet
          label={props.title}
          title={props.title}
          onClose={props.onClose}
          actions={props.actions}
          tone={props.tone}
          className="sheet-confirm"
        >
          {props.children}
        </Sheet>
      </Suspense>
    );
  return <CenterModal {...props} />;
}

function CenterModal({ title, children, onClose, actions, tone = "default" }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className={`modal ${tone}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h3>{title}</h3>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">{actions}</div>
      </div>
    </div>
  );
}
