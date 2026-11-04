import { useQuery } from "@tanstack/react-query";
import { ExtendedHoursCard } from "../ExtendedHoursCard";
import { ScheduleSettings } from "../ScheduleSettings";
import { scheduleQuery } from "../settingsQueries";
import { SectionGate } from "./SectionGate";

/** ⏰ 판단 일정 — 시장마다 따로(카드 안 국장·미장 탭). 처음에는 위 전환에서 보고 있는 시장 탭으로 연다.
 *  아래에 🌙 장외 주문(프리·애프터마켓, 미장 데이마켓) 켜기·끄기 (D-57) */
export function ScheduleSection() {
  const kr = useQuery(scheduleQuery("KR"));
  const us = useQuery(scheduleQuery("US"));
  const ready = !kr.isPending && !us.isPending;
  return (
    <SectionGate ready={ready} heights={[420]} label="판단 일정 불러오는 중">
      <ScheduleSettings />
      <ExtendedHoursCard />
    </SectionGate>
  );
}
