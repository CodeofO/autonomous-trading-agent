import { memo, useEffect, useRef } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Currency, Marker } from "../api/types";

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
const cents = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 가격 눈금: 국장은 원 단위 정수, 미장은 센트(소수 둘째 자리)까지
const priceFormatter = (currency: Currency) => (p: number) =>
  currency === "USD" ? cents.format(p) : won.format(p);
const pricePrecision = (currency: Currency) =>
  currency === "USD" ? { precision: 2, minMove: 0.01 } : { precision: 0, minMove: 1 };

const fixedFormatter = (digits: number) => {
  const f = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (p: number) => f.format(p);
};

function baseOptions(currency: Currency = "KRW") {
  return {
    autoSize: true,
    localization: { locale: "ko-KR", priceFormatter: priceFormatter(currency) },
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

// ---------- 같은 데이터인지 (새 배열이어도 내용이 같으면 차트에 다시 넣지 않는다) ----------
export interface EquityPoint {
  t: string;
  v: number;
}

const sameList = <T,>(a: readonly T[] | null, b: readonly T[], eq: (x: T, y: T) => boolean) =>
  a !== null && (a === b || (a.length === b.length && a.every((x, i) => eq(x, b[i]))));

const samePoints = (a: readonly EquityPoint[] | null, b: readonly EquityPoint[]) =>
  sameList(a, b, (x, y) => x.t === y.t && x.v === y.v);

const sameBars = (a: readonly Bar[] | null, b: readonly Bar[]) =>
  sameList(
    a,
    b,
    (x, y) =>
      x.time === y.time &&
      x.open === y.open &&
      x.high === y.high &&
      x.low === y.low &&
      x.close === y.close &&
      x.volume === y.volume,
  );

const sameMarkers = (a: readonly Marker[] | null, b: readonly Marker[]) =>
  sameList(a, b, (x, y) => x.time === y.time && x.side === y.side && x.kind === y.kind && x.text === y.text);

/**
 * 종목 일봉 + 거래량 + 체결·AI 결정 표시.
 * 차트는 한 번만 만들고(통화가 바뀔 때만 다시 — 눈금 형식이 달라진다), 데이터가 바뀌면 그 부분만 다시 넣는다.
 */
export const CandleChart = memo(function CandleChart({
  bars,
  markers,
  currency = "KRW",
  intraday = false,
  digits,
}: {
  bars: Bar[];
  markers: Marker[];
  currency?: Currency;
  /** 분 단위 봉 — 가로축에 시각까지 (D-63) */
  intraday?: boolean;
  /** 지수·금리처럼 통화가 아닌 값의 소수 자릿수 (주면 currency 대신) */
  digits?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const api = useRef<{
    chart: IChartApi;
    candles: ISeriesApi<"Candlestick">;
    vol: ISeriesApi<"Histogram">;
    markers: ISeriesMarkersPluginApi<Time> | undefined;
    bars: Bar[] | null;
    marks: Marker[] | null;
  } | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const opts = baseOptions(currency);
    if (digits != null) opts.localization.priceFormatter = fixedFormatter(digits);
    const chart = createChart(ref.current, opts);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceFormat: {
        type: "price",
        ...(digits != null ? { precision: digits, minMove: 1 / 10 ** digits } : pricePrecision(currency)),
      },
    });
    // 거래량 마지막 값은 가격 눈금에 표시하지 않는다 (가격처럼 읽히지 않게)
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    api.current = { chart, candles, vol, markers: undefined, bars: null, marks: null };
    return () => {
      chart.remove();
      api.current = null;
    };
  }, [currency, digits]);

  // 분 단위 봉이면 가로축에 시:분까지
  useEffect(() => {
    api.current?.chart.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } });
  }, [intraday, currency, digits]);

  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const barsChanged = !sameBars(a.bars, bars);
    if (barsChanged) {
      a.candles.setData(bars.map((b) => ({ ...b, time: b.time as Time })));
      a.vol.setData(
        bars.map((b) => ({
          time: b.time as Time,
          value: b.volume,
          color: b.close >= b.open ? "rgba(240,68,82,0.35)" : "rgba(49,130,246,0.35)",
        })),
      );
      a.bars = bars;
    }
    if (barsChanged || !sameMarkers(a.marks, markers)) {
      const days = new Set(bars.map((b) => b.time));
      const ms: SeriesMarker<Time>[] = markers
        .filter((m) => days.has(m.time))
        .sort((x, y) => String(x.time).padStart(12, "0").localeCompare(String(y.time).padStart(12, "0")))
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
      if (a.markers) a.markers.setMarkers(ms);
      else a.markers = createSeriesMarkers(a.candles, ms);
      a.marks = markers;
    }
    if (barsChanged) a.chart.timeScale().fitContent();
  }, [bars, markers, currency, digits]);

  return <div className="chart" ref={ref} data-testid="candle-chart" />;
});

/** 같은 초에 찍힌 스냅샷은 마지막 값만 사용 (시간은 엄격 증가해야 함) */
function equityData(points: readonly EquityPoint[]) {
  const byTime = new Map<number, number>();
  for (const p of points) {
    const t = p.t.length <= 10 ? Date.parse(p.t + "T00:00:00+09:00") : Date.parse(p.t);
    byTime.set(Math.floor(t / 1000) + KST_OFFSET_SEC, p.v);
  }
  return [...byTime.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, v]) => ({ time: t as UTCTimestamp, value: v }));
}

/**
 * 자산 추이 (영역 그래프). 점이 하나라도 생기면 차트를 한 번 만들고, 이후에는 내용이 바뀐 때만 setData.
 * 점이 없으면 그리지 않는다(빈 자리만).
 */
export const EquityChart = memo(function EquityChart({
  points,
  height = 220,
  currency = "KRW",
}: {
  points: EquityPoint[];
  height?: number;
  currency?: Currency;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; series: ISeriesApi<"Area">; shown: EquityPoint[] | null } | null>(
    null,
  );
  const hasData = points.length > 0;

  useEffect(() => {
    if (!ref.current || !hasData) return;
    const chart = createChart(ref.current, {
      ...baseOptions(currency),
      timeScale: { timeVisible: true, borderVisible: false },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: cssVar("--accent", "#3182f6"),
      topColor: "rgba(49,130,246,0.35)",
      bottomColor: "rgba(49,130,246,0.02)",
      lineWidth: 2,
    });
    api.current = { chart, series, shown: null };
    return () => {
      chart.remove();
      api.current = null;
    };
  }, [currency, hasData]);

  useEffect(() => {
    const a = api.current;
    if (!a || samePoints(a.shown, points)) return;
    a.series.setData(equityData(points));
    a.chart.timeScale().fitContent();
    a.shown = points;
  }, [points, currency, hasData]);

  return <div className="chart" style={{ height }} ref={ref} data-testid="equity-chart" />;
});
