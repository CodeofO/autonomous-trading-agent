import type { Indicator } from "../api/types";
import { useMarketIndicators } from "../hooks";
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

/** 대시보드 맨 위 '주요 지표' 한 줄. 넓은 화면은 한 줄 격자, 휴대폰은 옆으로 넘겨 보는 줄 */
export function IndicatorStrip() {
  const { data, isPending } = useMarketIndicators();

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
    <section className="ind-strip" aria-label="주요 지표">
      {data.items.map((it) => (
        <IndicatorCard key={it.key} item={it} asOf={data.as_of} />
      ))}
    </section>
  );
}

function IndicatorCard({ item, asOf }: { item: Indicator; asOf: string }) {
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
    <div className={`ind-card${dir ? ` ${dir}` : ""}${failed ? " failed" : ""}`} title={title}>
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
    </div>
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
