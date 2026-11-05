import type { EquityPoint } from "../components/charts";

/**
 * 자산 추이 끝에 '지금' 점을 붙인다 — 차트 마지막 값이 위 총자산(실시간 시세, 10초마다)과 같게.
 * 스냅샷은 10분마다(장중·켠 장외 세션)·마감 뒤에만 쌓이므로, 그 사이에는 차트 끝이 총자산보다 늦다.
 * 스냅샷이 하나도 없으면(그릴 곡선이 없음) 그대로, 마지막 스냅샷이 지금 값보다 새것이면 그대로.
 */
export function withLivePoint(
  points: EquityPoint[],
  live: { as_of?: string | null; equity?: number | null } | null | undefined,
): EquityPoint[] {
  if (!points.length || !live?.as_of || live.equity == null || !Number.isFinite(live.equity)) return points;
  const now = Date.parse(live.as_of);
  const last = Date.parse(points[points.length - 1].t);
  if (!Number.isFinite(now) || (Number.isFinite(last) && last >= now)) return points;
  return [...points, { t: live.as_of, v: live.equity }];
}
