import { useEffect, useRef, useState } from "react";
import { get } from "../api/client";
import type { ChartData, Indicator } from "../api/types";
import { useMarketIndicators } from "../hooks";
import { ChartView, IntervalPicker } from "./ChartPanel";
import { isIntraday, useChartData, useChartInterval } from "./chartData";
import { Sheet } from "./Sheet";
import { fmtDateTime, fmtIndicator, fmtIndicatorChange, indicatorDirection } from "../utils/format";
import { AnimatedNumber } from "./AnimatedNumber";

const SKELETON_CARDS = 6;

/** 단위별 글자 함수 (같은 단위면 같은 함수 — 숫자 칸이 다시 그려지지 않게) */
const formats = new Map<string, (v: number | null | undefined) => string>();
const formatFor = (unit: string) => {
  let f = formats.get(unit);
  if (!f) {
    f = (v) => fmtIndicator(v, unit);
    formats.set(unit, f);
  }
  return f;
};

/** 대시보드 맨 위 '주요 지표' 한 줄. 넓은 화면은 한 줄 격자, 휴대폰은 옆으로 넘겨 보는 줄. 누르면 지표 전부의 차트 창 */
export function IndicatorStrip() {
  const { data, isPending } = useMarketIndicators();
  // 누른 지표 키 (보드에서 그 칸을 강조) — 열려 있는 동안에도 값은 최신 목록으로 그린다
  const [open, setOpen] = useState<string | null>(null);

  if (isPending) {
    return (
      <section className="ind-strip" aria-label="주요 지표" aria-busy="true">
        {Array.from({ length: SKELETON_CARDS }, (_, i) => (
          <div key={i} className="ind-card skel" aria-hidden>
            <span className="ind-head">&nbsp;</span>
            <b className="ind-price">&nbsp;</b>
            <span className="ind-chg">&nbsp;</span>
            <span className="ind-spark" />
          </div>
        ))}
      </section>
    );
  }
  // 처음부터 못 받았으면 줄 자체를 숨긴다 (한 번이라도 받았으면 직전 값을 계속 보여 준다)
  if (!data?.items.length) return null;

  return (
    <>
      <section className="ind-strip" aria-label="주요 지표">
        {data.items.map((it) => (
          <IndicatorCard key={it.key} item={it} asOf={data.as_of} onOpen={setOpen} />
        ))}
      </section>
      {open && (
        <IndicatorBoard items={data.items} asOf={data.as_of} focus={open} onClose={() => setOpen(null)} />
      )}
    </>
  );
}

/** 주요 지수 차트 창 (D-65): 지표 전부를 한 창에 크게 — 봉 단위는 위에서 한 번 골라 모두에 적용한다 */
function IndicatorBoard({
  items,
  asOf,
  focus,
  onClose,
}: {
  items: Indicator[];
  asOf: string;
  focus: string;
  onClose: () => void;
}) {
  const [interval, pick] = useChartInterval("chart.interval.indicator");
  return (
    <Sheet
      size="wide"
      label="주요 지수 차트"
      title={
        <span>
          주요 지수 차트 <small className="muted">{fmtDateTime(asOf)} 기준</small>
        </span>
      }
      onClose={onClose}
      className="ind-board"
    >
      <IntervalPicker value={interval} onPick={pick} />
      <ul className="ind-board-grid">
        {items.map((it) => (
          <IndicatorTile key={it.key} item={it} interval={interval} focused={it.key === focus} />
        ))}
      </ul>
    </Sheet>
  );
}

const loadIndicatorChart = (key: string) => (iv: string) =>
  get<ChartData>(`/api/market/indicators/${key}/chart?interval=${iv}`);

/** 보드의 지표 한 칸: 이름·값·등락 + 차트 (환율은 봉이 없어 값만) */
function IndicatorTile({ item, interval, focused }: { item: Indicator; interval: string; focused: boolean }) {
  const ref = useRef<HTMLLIElement>(null);
  // 누른 칸이 보이게 — 연 순간 한 번만 (봉 단위를 바꿀 때마다 끌려가지 않게)
  const scrollOnOpen = useRef(focused);
  useEffect(() => {
    if (scrollOnOpen.current) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  const failed = item.error || item.price == null;
  const dir = failed ? null : indicatorDirection(item);
  return (
    <li ref={ref} className={`ind-tile${focused ? " focus" : ""}`} data-testid={`ind-tile-${item.key}`}>
      <div className="ind-tile-head">
        <span className="ind-tile-name">
          <b>{item.name}</b>
          {item.note && <small className="muted">{item.note}</small>}
        </span>
        <span className="ind-tile-price">
          <b className={`num ${dir ?? ""}`}>{failed ? "—" : fmtIndicator(item.price, item.unit)}</b>
          <small className={`ind-chg ${dir ?? ""}`}>{failed ? "" : fmtIndicatorChange(item)}</small>
        </span>
      </div>
      {item.kind === "fx" ? (
        <p className="muted ind-tile-none">
          토스증권 Open API 에 환율 차트(봉)가 없어 현재 환율만 보여 줘요.
        </p>
      ) : (
        <TileChart item={item} interval={interval} />
      )}
    </li>
  );
}

function TileChart({ item, interval }: { item: Indicator; interval: string }) {
  // 국고채는 Open API 가 일봉만 준다 — 분 단위를 골랐으면 일봉으로
  const dailyOnly = item.kind === "bond";
  const iv = dailyOnly && isIntraday(interval) ? "1d" : interval;
  const q = useChartData(["indicator", item.key], loadIndicatorChart(item.key), iv, { slow: true });
  const note =
    item.kind === "etf"
      ? `미국 지수는 토스 Open API 에 없어 지수를 따라가는 ${item.note ?? "ETF"}로 보여 줘요.`
      : dailyOnly && iv !== interval
        ? "국고채는 일봉만 있어요."
        : null;
  return (
    <>
      <ChartView
        q={q}
        currency={item.kind === "etf" ? "USD" : undefined}
        digits={item.kind === "bond" ? 3 : item.kind === "etf" ? undefined : 2}
      />
      {note && <p className="muted legend">{note}</p>}
    </>
  );
}

function IndicatorCard({
  item,
  asOf,
  onOpen,
}: {
  item: Indicator;
  asOf: string;
  onOpen: (key: string) => void;
}) {
  const failed = item.error || item.price == null;
  const dir = failed ? null : indicatorDirection(item);
  const change = failed ? "" : fmtIndicatorChange(item);
  const title = failed
    ? `${item.name}: 지금은 값을 불러오지 못했어요`
    : [
        item.note ? `${item.name} (${item.note})` : item.name,
        `전일 종가 ${fmtIndicator(item.prev_close, item.unit)}`,
        `기준 ${fmtDateTime(asOf)}`,
      ].join("\n");

  return (
    <button
      type="button"
      className={`ind-card${dir ? ` ${dir}` : ""}${failed ? " failed" : ""}`}
      title={`${title}\n(눌러서 차트 보기)`}
      aria-label={`${item.name} 차트 보기`}
      onClick={() => onOpen(item.key)}
    >
      <span className="ind-head">
        <span className="ind-name">{item.name}</span>
        {item.note && <small className="ind-note">{item.note}</small>}
      </span>
      <b className="ind-price">
        {failed ? "—" : <AnimatedNumber value={item.price} format={formatFor(item.unit)} />}
      </b>
      <span
        className={`ind-chg ${dir ?? ""}`}
        aria-label={
          item.kind === "fx" && dir ? (dir === "up" ? "전일 대비 상승" : "전일 대비 하락") : undefined
        }
      >
        {change || " "}
      </span>
      {failed ? <span className="ind-spark" /> : <Sparkline values={item.spark} fallbackDir={dir} />}
    </button>
  );
}

const W = 100;
const H = 24;
const PAD = 2;

/** 아주 작은 선 그래프 (차트 라이브러리 없이 SVG 하나). 점이 2개 미만이면 자리만 차지한다 */
function Sparkline({ values, fallbackDir }: { values: number[]; fallbackDir: "up" | "down" | null }) {
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length < 2) return <span className="ind-spark" />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = W / (pts.length - 1);
  const d = pts
    .map((v, i) => {
      const x = i * step;
      const y = max === min ? H / 2 : PAD + (1 - (v - min) / span) * (H - PAD * 2);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const last = pts[pts.length - 1];
  const dir = fallbackDir ?? (last > pts[0] ? "up" : last < pts[0] ? "down" : null);
  return (
    <svg
      className={`ind-spark ${dir ?? ""}`}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden
      data-testid="sparkline"
    >
      <polyline
        points={d}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
