import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "../api/client";
import type { Bar, Currency, Marker } from "../api/types";
import { currencyOf, MARKET_META, useMarket } from "../market";
import type { StockTarget } from "../ui/overlays";
import { fmtNum, fmtPct, money, price, PRICE_FMT, signClass } from "../utils/format";
import { AnimatedNumber } from "./AnimatedNumber";
import { Sheet } from "./Sheet";
import { SkeletonLines } from "./Skeleton";

/** 미니 차트에 쓰는 최근 거래일 수 */
const MINI_DAYS = 60;

/**
 * 종목 빠른 보기 (휴대폰 아래 시트) — 보유·관심 종목 줄을 누르면 화면을 옮기지 않고 가격·등락·최근 흐름·내 보유를 본다.
 * 일봉은 종목 화면과 같은 캐시(같은 키)라 '종목 상세'를 열면 바로 그려진다
 */
export function StockSheet({ target, onClose }: { target: StockTarget; onClose: () => void }) {
  const { symbol, market, position } = target;
  const api = marketApi(market);
  const cur = currencyOf(market);
  const meta = MARKET_META[market];
  const { view, focusMarket } = useMarket();
  const candles = useQuery({
    queryKey: api.key("candles", symbol),
    queryFn: () =>
      api.get<{ name: string; bars: Bar[]; markers: Marker[] }>(`/api/market/${symbol}/candles?days=180`),
    staleTime: 60_000,
  });
  const bars = candles.data?.bars ?? [];
  const last = bars.at(-1);
  const prev = bars.at(-2);
  const name = target.name || candles.data?.name || symbol;
  const now = position?.last_price ?? last?.close ?? null;
  const base = prev?.close ?? null;
  const change = now != null && base ? now - base : null;
  const changePct = change != null && base ? (change / base) * 100 : null;
  const go = () => {
    // '전체'에서 열었으면 그 시장 화면으로 (한눈에 보기의 보유 종목처럼)
    if (view !== market) focusMarket(market);
    onClose();
  };

  return (
    <Sheet label={`${name} 빠른 보기`} onClose={onClose} className="stock-sheet">
      <div className="qs-head">
        <div className="qs-name">
          <b>{name}</b>
          <small className="muted">
            {meta.flag} {symbol}
          </small>
        </div>
        <div className="qs-price">
          <b className={`num ${signClass(change)}`}>
            {now == null ? (
              candles.isPending ? (
                <i className="skel-inline" aria-hidden />
              ) : (
                "—"
              )
            ) : (
              <AnimatedNumber value={now} format={PRICE_FMT[cur]} />
            )}
          </b>
          {change != null && (
            <small className={`num ${signClass(change)}`}>
              {change > 0 ? "▲" : change < 0 ? "▼" : ""} {price(Math.abs(change), cur)} ({fmtPct(changePct)})
            </small>
          )}
          {last && <small className="muted">전일 종가 대비 · {last.time}</small>}
        </div>
      </div>

      {candles.isPending ? (
        <SkeletonLines lines={3} label="최근 흐름 불러오는 중" />
      ) : bars.length >= 2 ? (
        <MiniChart closes={bars.slice(-MINI_DAYS).map((b) => b.close)} cur={cur} />
      ) : (
        <p className="muted qs-none">
          {candles.isError ? "일봉을 불러오지 못했어요." : "일봉이 아직 없어요."}
        </p>
      )}

      {position && (
        <dl className="kv-grid qs-pos" aria-label="내 보유">
          {position.quantity != null && (
            <div>
              <dt>보유</dt>
              <dd className="num">{fmtNum(position.quantity)}주</dd>
            </div>
          )}
          {position.avg_price != null && (
            <div>
              <dt>평단</dt>
              <dd className="num">{price(position.avg_price, cur)}</dd>
            </div>
          )}
          {position.market_value != null && (
            <div>
              <dt>평가금액</dt>
              <dd className="num">{money(position.market_value, cur)}</dd>
            </div>
          )}
          <div>
            <dt>수익률</dt>
            <dd className={`num ${signClass(position.pnl_pct)}`}>
              {fmtPct(position.pnl_pct)}
              {position.unrealized_pnl != null && (
                <small> ({money(position.unrealized_pnl, cur, { sign: true })})</small>
              )}
            </dd>
          </div>
          <div>
            <dt>비중</dt>
            <dd className="num">{position.weight_pct.toFixed(1)}%</dd>
          </div>
        </dl>
      )}

      <div className="qs-actions">
        <Link to={`/stock/${symbol}`} className="btn primary" onClick={go}>
          종목 상세 보기 →
        </Link>
        <button type="button" className="btn ghost" onClick={onClose}>
          닫기
        </button>
      </div>
    </Sheet>
  );
}

const W = 100;
const H = 40;

/** 최근 종가 흐름 (차트 라이브러리 없이 SVG 하나 — 시트를 열 때 차트 조각을 받지 않는다) */
function MiniChart({ closes, cur }: { closes: number[]; cur: Currency }) {
  const pts = closes.filter((v) => Number.isFinite(v));
  if (pts.length < 2) return null;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = W / (pts.length - 1);
  const xy = pts.map((v, i) => [i * step, 2 + (1 - (v - min) / span) * (H - 4)] as const);
  const line = xy.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  const dir = pts[pts.length - 1] > pts[0] ? "up" : pts[pts.length - 1] < pts[0] ? "down" : "";
  return (
    <figure className={`qs-chart ${dir}`} aria-label={`최근 ${pts.length}거래일 종가 흐름`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden data-testid="mini-chart">
        <polygon points={area} className="qs-area" />
        <polyline
          points={line}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <figcaption className="muted">
        최근 {pts.length}거래일 · 최고 {price(max, cur)} · 최저 {price(min, cur)}
      </figcaption>
    </figure>
  );
}
