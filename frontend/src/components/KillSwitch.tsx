import { Icon } from "./Icon";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { errorText, marketApi } from "../api/client";
import type { MarketCode } from "../api/types";
import { invalidateNames, MARKET_META, useMarket } from "../market";
import { preloadSheet } from "../ui/overlays";
import { toast } from "../ui/toast";
import { Modal } from "./Modal";

/** 킬 스위치. 기본은 지금 시장 하나 — '전체' 보기에서는 targets 로 받은 시장을 한꺼번에 멈추고 다시 켠다 */
export function KillSwitch({ status, targets }: { status: string; targets?: MarketCode[] }) {
  const qc = useQueryClient();
  const { market } = useMarket();
  const list = targets ?? [market];
  const many = list.length > 1;
  const names = list.map((m) => MARKET_META[m].name).join("·");
  const [open, setOpen] = useState(false);
  // 엔진 상태가 보이는 곳(상단 바·매니저·구조·시장 목록·한눈에 보기)과, 정지하면 취소되는 미체결 주문만 다시 받는다
  const refresh = () => {
    for (const m of list)
      void invalidateNames(
        qc,
        ["system", "agent-structure", "orders", "portfolio", "overview", "markets"],
        m,
      );
  };
  const kill = useMutation({
    mutationFn: () =>
      Promise.all(
        list.map((m) =>
          marketApi(m).post<{ status: string; canceled: number }>("/api/engine/kill", {
            reason: "UI 킬 스위치",
          }),
        ),
      ),
    onSuccess: (res) => {
      setOpen(false);
      refresh();
      const canceled = res.reduce((n, r) => n + (r?.canceled ?? 0), 0);
      toast(
        `${many ? `${names} ` : ""}자동매매를 멈췄어요${canceled ? ` · 미체결 주문 ${canceled}건 취소` : ""}`,
        5000,
      );
    },
  });
  const resume = useMutation({
    mutationFn: () => Promise.all(list.map((m) => marketApi(m).post("/api/engine/resume"))),
    onSuccess: () => {
      refresh();
      toast(`${many ? `${names} ` : ""}자동매매를 다시 켰어요`);
    },
    // 버튼 하나뿐이라 실패 이유를 보여 줄 자리가 없다 — 토스트로
    onError: (e) => toast.error(`재개하지 못했어요: ${errorText(e)}`),
  });

  if (status === "KILLED") {
    return (
      <button className="btn resume" onClick={() => resume.mutate()} disabled={resume.isPending}>
        <Icon name="play" size={14} /> {many ? `${names} 자동매매 재개` : "자동매매 재개"}
      </button>
    );
  }
  return (
    <>
      <button
        className="btn kill"
        onClick={() => setOpen(true)}
        onTouchStart={preloadSheet}
        aria-label="킬 스위치"
      >
        <Icon name="power" size={14} /> 킬 스위치
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
          <p>
            {many ? `${names} ` : ""}자동매매를 즉시 멈추고 미체결 주문을 모두 취소합니다. 에이전트 세션도
            실행되지 않습니다.
          </p>
          <p className="muted">모의투자 계좌에만 적용됩니다. 나중에 ‘재개’로 다시 켤 수 있어요.</p>
          {kill.isError && <p className="error">{errorText(kill.error)}</p>}
        </Modal>
      )}
    </>
  );
}
