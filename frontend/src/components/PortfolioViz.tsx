import {
  Fragment,
  memo,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Link } from "react-router-dom";
import type { Currency, MarketCode, Position } from "../api/types";
import { MARKET_META, readStore, useCurrency, useMarket, writeStore } from "../market";
import { isMobile } from "../mobile";
import { openStockSheet } from "../ui/overlays";
import {
  allocationItems,
  foldForDonut,
  pnlStep,
  type AllocItem,
  type AllocSource,
} from "../utils/allocation";
import { fmtPct, money } from "../utils/format";
import { squarify } from "../utils/treemap";
import { HoldingsTable } from "./HoldingsTable";
import "./PortfolioViz.css";

/*
 * 포트폴리오 시각화 (D-69): 보유 종목을 표 · 비중(도넛) · 모자이크(트리맵)로.
 * 차트 라이브러리 없이 SVG·HTML 만 — 처음 받는 조각이 커지지 않는다.
 */

export type HoldingsMode = "table" | "donut" | "mosaic";
const MODE_LABEL: Record<HoldingsMode, string> = { table: "표", donut: "비중", mosaic: "모자이크" };

/** 고른 보기 (이 기기에 기억) */
function useMode(
  storageKey: string,
  modes: readonly HoldingsMode[],
): [HoldingsMode, (m: HoldingsMode) => void] {
  const [mode, setMode] = useState<HoldingsMode>(() => {
    const saved = readStore(storageKey) as HoldingsMode | null;
    return saved && modes.includes(saved) ? saved : modes[0];
  });
  const pick = (m: HoldingsMode) => {
    setMode(m);
    writeStore(storageKey, m);
  };
  return [mode, pick];
}

function ModeSwitch({
  mode,
  modes,
  onPick,
}: {
  mode: HoldingsMode;
  modes: readonly HoldingsMode[];
  onPick: (m: HoldingsMode) => void;
}) {
  return (
    <div className="seg viz-modes" role="group" aria-label="보유 종목 보기">
      {modes.map((m) => (
        <button
          key={m}
          type="button"
          className={m === mode ? "active" : ""}
          aria-pressed={m === mode}
          onClick={() => onPick(m)}
        >
          {MODE_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

const TABLE_MODES: readonly HoldingsMode[] = ["table", "donut", "mosaic"];
const CHART_MODES: readonly HoldingsMode[] = ["donut", "mosaic"];

/**
 * '보유 종목' 카드: 머리줄에 표 · 비중 · 모자이크 전환. 시장 하나의 보유 종목·현금.
 * (전체 보기의 시장별 카드도 MarketScope 안에서 이것을 쓴다)
 */
export function HoldingsCard({
  title,
  positions,
  cash,
  loading = false,
}: {
  title: ReactNode;
  positions: Position[];
  cash: number | null | undefined;
  loading?: boolean;
}) {
  const cur = useCurrency();
  const { market } = useMarket();
  const [mode, pick] = useMode("portfolio.view", TABLE_MODES);
  const items = useMemo(() => allocationItems([{ market, positions, cash }]), [market, positions, cash]);
  const showChart = mode !== "table" && !loading && positions.length > 0;
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <ModeSwitch mode={mode} modes={TABLE_MODES} onPick={pick} />
      </div>
      {!showChart ? (
        <HoldingsTable positions={positions} loading={loading} />
      ) : mode === "donut" ? (
        <AllocationDonut items={items} currency={cur} />
      ) : (
        <AllocationMosaic items={items} currency={cur} />
      )}
    </section>
  );
}

/** 전체 보기(국장 + 미장): 미장을 원화로 바꿔 한 도넛·모자이크로 */
export function CombinedAllocation({ sources, fxNote }: { sources: AllocSource[]; fxNote: ReactNode }) {
  const [mode, pick] = useMode("portfolio.view.all", CHART_MODES);
  const items = useMemo(() => allocationItems(sources), [sources]);
  const stocks = items.filter((x) => x.kind === "stock").length;
  return (
    <section className="card" aria-label="전체 보유 비중">
      <div className="card-head">
        <h2>전체 보유 비중</h2>
        <ModeSwitch mode={mode} modes={CHART_MODES} onPick={pick} />
      </div>
      {items.length === 0 ? (
        <p className="muted empty">보유 종목·현금 정보를 아직 받지 못했어요.</p>
      ) : mode === "donut" ? (
        <AllocationDonut items={items} currency="KRW" combined />
      ) : (
        <AllocationMosaic items={items} currency="KRW" combined />
      )}
      <p className="muted viz-note">
        국장 {sources.find((s) => s.market === "KR")?.positions.length ?? 0}종목 · 미장{" "}
        {sources.find((s) => s.market === "US")?.positions.length ?? 0}종목 (합계 {stocks}종목). {fxNote}
      </p>
    </section>
  );
}

// ====================================================================== 공통
const flag = (m?: MarketCode) => (m ? MARKET_META[m].flag : "");

/** 종목 열기: 넓은 화면은 종목 화면으로(전체 보기면 그 시장으로 맞춰), 휴대폰은 빠른 보기 시트 */
function useOpenStock(combined: boolean) {
  const { focusMarket, market: here } = useMarket();
  return (e: MouseEvent, it: AllocItem) => {
    if (it.kind !== "stock") return;
    const market = it.market ?? here;
    if (isMobile()) {
      e.preventDefault();
      openStockSheet({ symbol: it.symbol, name: it.name, market, position: it.position });
      return;
    }
    if (combined) focusMarket(market);
  };
}

function itemTitle(it: AllocItem, currency: Currency, combined: boolean): string {
  const head =
    it.kind === "stock" ? `${combined ? `${flag(it.market)} ` : ""}${it.name} (${it.symbol})` : it.name;
  return [
    head,
    `비중 ${it.weight.toFixed(1)}% · ${money(it.value, currency)}`,
    ...(it.pnlPct != null ? [`수익률 ${fmtPct(it.pnlPct)}`] : []),
  ].join("\n");
}

// ====================================================================== 도넛
const TAU = Math.PI * 2;
const C = 100; // 가운데
const R_OUT = 92;
const R_IN = 60;
const LIFT = 5; // 고른 조각만 바깥으로

function pt(r: number, a: number): string {
  return `${(C + r * Math.sin(a)).toFixed(3)} ${(C - r * Math.cos(a)).toFixed(3)}`;
}

/** 12시 방향에서 시계 방향으로 a0 → a1 인 고리 조각 */
function arc(r0: number, r1: number, a0: number, a1: number): string {
  if (a1 - a0 >= TAU - 1e-6) {
    // 한 종목이 100% — 반원 두 개로 닫는다
    const mid = a0 + Math.PI;
    return [
      `M ${pt(r1, a0)} A ${r1} ${r1} 0 1 1 ${pt(r1, mid)} A ${r1} ${r1} 0 1 1 ${pt(r1, a0)}`,
      `M ${pt(r0, a0)} A ${r0} ${r0} 0 1 0 ${pt(r0, mid)} A ${r0} ${r0} 0 1 0 ${pt(r0, a0)} Z`,
    ].join(" ");
  }
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return [
    `M ${pt(r1, a0)}`,
    `A ${r1} ${r1} 0 ${large} 1 ${pt(r1, a1)}`,
    `L ${pt(r0, a1)}`,
    `A ${r0} ${r0} 0 ${large} 0 ${pt(r0, a0)}`,
    "Z",
  ].join(" ");
}

/** 조각 색: 종목은 범주형 1~8 (큰 순서), 기타·현금은 회색 둘 */
const swatchClass = (it: AllocItem, i: number) =>
  it.kind === "cash" ? "viz-cash" : it.kind === "other" ? "viz-other" : `viz-s${(i % 8) + 1}`;

export const AllocationDonut = memo(function AllocationDonut({
  items,
  currency,
  combined = false,
}: {
  items: AllocItem[];
  currency: Currency;
  combined?: boolean;
}) {
  const [active, setActive] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const open = useOpenStock(combined);
  const slices = useMemo(() => {
    const folded = foldForDonut(items);
    const total = folded.reduce((s, x) => s + x.value, 0);
    let a = 0;
    let stockIdx = 0;
    return folded.map((it) => {
      const span = total > 0 ? (it.value / total) * TAU : 0;
      const cls = swatchClass(it, it.kind === "stock" ? stockIdx++ : 0);
      const s = { it, a0: a, a1: a + span, cls };
      a += span;
      return s;
    });
  }, [items]);
  const total = items.reduce((s, x) => s + x.value, 0);
  const stocks = items.filter((x) => x.kind === "stock").length;
  const cashW = items.filter((x) => x.kind === "cash").reduce((s, x) => s + x.weight, 0);
  const cur = slices.find((s) => s.it.key === active)?.it;
  const label = `보유 비중: ${slices.map((s) => `${s.it.name} ${s.it.weight.toFixed(1)}%`).join(", ")}`;

  return (
    <div className="viz-donut" data-testid="alloc-donut">
      <div className="viz-donut-chart">
        <svg viewBox="0 0 200 200" role="img" aria-label={label} onMouseLeave={() => setActive(null)}>
          {slices.map(({ it, a0, a1, cls }) => (
            <path
              key={it.key}
              d={arc(R_IN, it.key === active ? R_OUT + LIFT : R_OUT, a0, a1)}
              className={`viz-slice ${cls}${active && it.key !== active ? " dim" : ""}`}
              onMouseEnter={() => setActive(it.key)}
              onClick={() => setActive((k) => (k === it.key ? null : it.key))}
            >
              <title>{itemTitle(it, currency, combined)}</title>
            </path>
          ))}
        </svg>
        <div className="viz-donut-center" aria-live="polite">
          {cur ? (
            <>
              <small>
                {cur.kind === "stock" ? `${combined ? flag(cur.market) + " " : ""}${cur.symbol}` : " "}
              </small>
              <b className="viz-center-name">{cur.name}</b>
              <b className="num viz-center-big">{cur.weight.toFixed(1)}%</b>
              <small className="num">{money(cur.value, currency)}</small>
              {cur.pnlPct != null && (
                <small className={`num ${cur.pnlPct > 0 ? "up" : cur.pnlPct < 0 ? "down" : ""}`}>
                  {fmtPct(cur.pnlPct)}
                </small>
              )}
            </>
          ) : (
            <>
              {/* 전체 보기의 합계는 위 '전체 자산' 카드에 있다 (받는 시각이 달라 몇 원 어긋나 보이지 않게 다시 쓰지 않는다) */}
              <small>{combined ? "국장 + 미장" : "합계"}</small>
              <b className="num viz-center-big">{combined ? `${stocks}종목` : money(total, currency)}</b>
              <small>
                {combined ? "" : `${stocks}종목 · `}현금 {cashW.toFixed(1)}%
              </small>
            </>
          )}
        </div>
      </div>
      <ul className="viz-legend" aria-label="종목별 비중">
        {slices.map(({ it, cls }) => (
          <Fragment key={it.key}>
            <LegendRow
              it={it}
              cls={cls}
              on={it.key === active}
              currency={currency}
              combined={combined}
              onActive={setActive}
              onOpen={open}
              expanded={expanded}
              onExpand={() => setExpanded((x) => !x)}
            />
            {/* 펼친 '기타'의 종목은 바로 아래에 */}
            {it.kind === "other" &&
              expanded &&
              it.children?.map((c) => (
                <LegendRow
                  key={c.key}
                  it={c}
                  cls="viz-other"
                  sub
                  on={false}
                  currency={currency}
                  combined={combined}
                  onActive={(k) => setActive(k ? "other" : null)}
                  onOpen={open}
                />
              ))}
          </Fragment>
        ))}
      </ul>
    </div>
  );
});

/** 범례 한 줄: 색 · 이름(티커) · 비중 · 금액 · 수익률. '기타'는 눌러서 묶인 종목을 펼친다 */
function LegendRow({
  it,
  cls,
  on,
  sub = false,
  currency,
  combined,
  onActive,
  onOpen,
  expanded,
  onExpand,
}: {
  it: AllocItem;
  cls: string;
  on: boolean;
  sub?: boolean;
  currency: Currency;
  combined: boolean;
  onActive: (key: string | null) => void;
  onOpen: (e: MouseEvent, it: AllocItem) => void;
  expanded?: boolean;
  onExpand?: () => void;
}) {
  return (
    <li
      className={`${on ? "on" : ""}${sub ? " sub" : ""}`.trim() || undefined}
      onMouseEnter={() => onActive(it.key)}
      onMouseLeave={() => onActive(null)}
      onFocus={() => onActive(it.key)}
      onBlur={() => onActive(null)}
    >
      <i className={`viz-sw ${cls}`} aria-hidden />
      <span className="viz-legend-name">
        {it.kind === "stock" ? (
          <Link to={`/stock/${it.symbol}`} onClick={(e) => onOpen(e, it)}>
            {combined && <span aria-hidden>{flag(it.market)} </span>}
            {it.name}
          </Link>
        ) : it.kind === "other" && onExpand ? (
          <button type="button" className="viz-expand" aria-expanded={!!expanded} onClick={onExpand}>
            {it.name} {expanded ? "▴" : "▾"}
          </button>
        ) : (
          <span>{it.name}</span>
        )}
        {it.kind === "stock" && it.symbol !== it.name && <small className="muted"> {it.symbol}</small>}
      </span>
      <b className="num viz-legend-w">{it.weight.toFixed(1)}%</b>
      <span className="num muted viz-legend-v">{money(it.value, currency)}</span>
      <span
        className={`num viz-legend-p ${it.pnlPct == null ? "muted" : it.pnlPct > 0 ? "up" : it.pnlPct < 0 ? "down" : ""}`}
      >
        {it.pnlPct == null ? "—" : fmtPct(it.pnlPct)}
      </span>
    </li>
  );
}

// ====================================================================== 모자이크
/** 상자 크기 (jsdom·첫 그림에서 0 이면 기본값으로 배치) */
function useBoxSize<T extends HTMLElement>(): [RefObject<T | null>, { w: number; h: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () =>
      setSize((s) =>
        s.w === el.clientWidth && s.h === el.clientHeight ? s : { w: el.clientWidth, h: el.clientHeight },
      );
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

const FALLBACK = { w: 640, h: 320 };
const GAP = 1; // 칸 사이 2px (양쪽 1px씩) 바탕색 틈

/** 칸 크기에 따라 글자 양 (작으면 숨기고 툴팁·스크린리더 이름만) */
const sizeClass = (w: number, h: number) => (w >= 92 && h >= 58 ? "lg" : w >= 54 && h >= 34 ? "md" : "sm");

export const AllocationMosaic = memo(function AllocationMosaic({
  items,
  currency,
  combined = false,
}: {
  items: AllocItem[];
  currency: Currency;
  combined?: boolean;
}) {
  const [ref, size] = useBoxSize<HTMLDivElement>();
  const open = useOpenStock(combined);
  const box = size.w > 0 && size.h > 0 ? size : FALLBACK;
  const tiles = useMemo(() => squarify(items, { x: 0, y: 0, w: box.w, h: box.h }), [items, box.w, box.h]);
  return (
    <div className="viz-mosaic-wrap" data-testid="alloc-mosaic">
      <div
        className={`viz-mosaic${items.length > 12 ? " many" : ""}`}
        ref={ref}
        role="list"
        aria-label="보유 비중 모자이크 (넓이 = 비중, 색 = 수익률)"
      >
        {tiles.map((t) => {
          const step = t.kind === "cash" ? null : pnlStep(t.pnlPct);
          const cls = `viz-tile ${sizeClass(t.w, t.h)} ${step == null ? "cash" : `p${step < 0 ? "m" : "p"}${Math.abs(step)}`}`;
          const style = {
            left: t.x + GAP,
            top: t.y + GAP,
            width: Math.max(0, t.w - GAP * 2),
            height: Math.max(0, t.h - GAP * 2),
          };
          const body = (
            <>
              <span className="viz-tile-name">
                {combined && t.market && t.kind === "stock" && <span aria-hidden>{flag(t.market)} </span>}
                {t.name}
              </span>
              {t.kind === "stock" && t.symbol !== t.name && <span className="viz-tile-sym">{t.symbol}</span>}
              <span className="viz-tile-nums num">
                {t.weight.toFixed(1)}%{t.pnlPct != null && <> · {fmtPct(t.pnlPct)}</>}
              </span>
            </>
          );
          const title = itemTitle(t, currency, combined);
          return t.kind === "stock" ? (
            <Link
              key={t.key}
              role="listitem"
              to={`/stock/${t.symbol}`}
              className={cls}
              style={style}
              title={title}
              aria-label={title.replace(/\n/g, ", ")}
              onClick={(e) => open(e, t)}
            >
              {body}
            </Link>
          ) : (
            <div
              key={t.key}
              role="listitem"
              className={cls}
              style={style}
              title={title}
              aria-label={title.replace(/\n/g, ", ")}
            >
              {body}
            </div>
          );
        })}
      </div>
      <div className="viz-scale" aria-label="색: 수익률">
        <span className="muted">손실</span>
        {["pm3", "pm2", "pm1", "pp0", "pp1", "pp2", "pp3"].map((c) => (
          <i key={c} className={`viz-scale-sw ${c}`} aria-hidden />
        ))}
        <span className="muted">이익</span>
        <span className="muted viz-scale-note">경계 ±0.5 · 2 · 5% · 넓이 = 비중 · 회색 칸 = 현금</span>
      </div>
    </div>
  );
});
