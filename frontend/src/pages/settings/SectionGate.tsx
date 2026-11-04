import type { ReactNode } from "react";
import { SkeletonCards } from "../../components/Skeleton";

/**
 * 설정 주제 하나가 그릴 내용을 다 받을 때까지 자리 표시 한 번 (카드 안 내용이 따로 도착하며 밀리지 않게).
 * 실패해도 받은 것으로 친다 — 카드가 오류를 보여 준다
 */
export function SectionGate({
  ready,
  heights,
  label,
  children,
}: {
  ready: boolean;
  heights: number[];
  label: string;
  children: ReactNode;
}) {
  return ready ? <>{children}</> : <SkeletonCards heights={heights} label={label} />;
}
