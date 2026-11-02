import { useEffect, useRef } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Marker } from "../api/types";

// 한국 시장 관례: 상승 빨강 / 하락 파랑
const UP = "#f04452";
const DOWN = "#3182f6";

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// 차트 라이브러리는 UTC 로 시각을 표시하므로 KST(+9h) 만큼 밀어서 넣는다
const KST_OFFSET_SEC = 9 * 3600;
const won = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

function baseOptions() {
  return {
    autoSize: true,
    localization: { locale: "ko-KR", priceFormatter: (p: number) => won.format(p) },
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: cssVar("--text-dim", "#8b95a1"),
      fontFamily: "inherit",
    },
    grid: {
      vertLines: { color: cssVar("--grid", "rgba(128,128,128,0.12)") },
      horzLines: { color: cssVar("--grid", "rgba(128,128,128,0.12)") },
    },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
  };
}

export function CandleChart({ bars, markers }: { bars: Bar[]; markers: Marker[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart: IChartApi = createChart(ref.current, baseOptions());
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    candles.setData(bars.map((b) => ({ ...b, time: b.time as Time })));
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? "rgba(240,68,82,0.35)" : "rgba(49,130,246,0.35)",
      })),
    );
    const days = new Set(bars.map((b) => b.time));
    const ms: SeriesMarker<Time>[] = markers
      .filter((m) => days.has(m.time))
      .sort((a, b) => a.time.localeCompare(b.time))
      .map((m) => {
        const buy = m.side === "BUY";
        return {
          time: m.time as Time,
          position: buy ? "belowBar" : "aboveBar",
          color: m.kind === "fill" ? (buy ? UP : DOWN) : "#a78bfa",
          shape: m.kind === "fill" ? (buy ? "arrowUp" : "arrowDown") : "circle",
          text: m.text,
        };
      });
    createSeriesMarkers(candles, ms);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, markers]);
  return <div className="chart" ref={ref} data-testid="candle-chart" />;
}

export function EquityChart({
  points,
  height = 220,
}: {
  points: { t: string; v: number }[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      ...baseOptions(),
      timeScale: { timeVisible: true, borderVisible: false },
    });
    const s = chart.addSeries(AreaSeries, {
      lineColor: cssVar("--accent", "#3182f6"),
      topColor: "rgba(49,130,246,0.35)",
      bottomColor: "rgba(49,130,246,0.02)",
      lineWidth: 2,
    });
    // 같은 초에 찍힌 스냅샷은 마지막 값만 사용 (시간은 엄격 증가해야 함)
    const byTime = new Map<number, number>();
    for (const p of points) {
      const t = p.t.length <= 10 ? Date.parse(p.t + "T00:00:00+09:00") : Date.parse(p.t);
      byTime.set(Math.floor(t / 1000) + KST_OFFSET_SEC, p.v);
    }
    s.setData(
      [...byTime.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([t, v]) => ({ time: t as UTCTimestamp, value: v })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return <div className="chart" style={{ height }} ref={ref} data-testid="equity-chart" />;
}
