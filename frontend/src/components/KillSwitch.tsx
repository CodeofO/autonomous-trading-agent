import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { errorText, post } from "../api/client";
import { Modal } from "./Modal";

export function KillSwitch({ status }: { status: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const kill = useMutation({
    mutationFn: () =>
      post<{ status: string; canceled: number }>("/api/engine/kill", { reason: "UI 킬 스위치" }),
    onSuccess: () => {
      setOpen(false);
      qc.invalidateQueries();
    },
  });
  const resume = useMutation({
    mutationFn: () => post("/api/engine/resume"),
    onSuccess: () => qc.invalidateQueries(),
  });

  if (status === "KILLED") {
    return (
      <button className="btn resume" onClick={() => resume.mutate()} disabled={resume.isPending}>
        ▶ 자동매매 재개
      </button>
    );
  }
  return (
    <>
      <button className="btn kill" onClick={() => setOpen(true)} aria-label="킬 스위치">
        ⏻ 킬 스위치
      </button>
      {open && (
        <Modal
          title="킬 스위치를 작동할까요?"
          tone="danger"
          onClose={() => setOpen(false)}
          actions={
            <>
              <button className="btn ghost" onClick={() => setOpen(false)}>
                취소
              </button>
              <button className="btn kill" onClick={() => kill.mutate()} disabled={kill.isPending}>
                {kill.isPending ? "정지 중…" : "즉시 전체 정지"}
              </button>
            </>
          }
        >
          <p>자동매매를 즉시 멈추고 미체결 주문을 모두 취소합니다. 에이전트 세션도 실행되지 않습니다.</p>
          <p className="muted">모의투자 계좌에만 적용됩니다. 나중에 ‘재개’로 다시 켤 수 있어요.</p>
          {kill.isError && <p className="error">{errorText(kill.error)}</p>}
        </Modal>
      )}
    </>
  );
}
