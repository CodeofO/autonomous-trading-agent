import { memo, useCallback, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import type { Currency, Position } from "../api/types";
import { useCurrency, useMarket } from "../market";
import { isMobile } from "../mobile";
import { openStockSheet } from "../ui/overlays";
import { fmtNum, MONEY_FMT, pctFmt, price, PRICE_FMT, signClass } from "../utils/format";
import { AnimatedNumber } from "./AnimatedNumber";
import { SkeletonLines } from "./Skeleton";

/**
 * 보유 종목 표 (대시보드·포트폴리오). 받는 중에는 '없어요' 대신 자리 표시.
 * 현재가·평가금액·수익률은 바뀌면 굴러가며 바뀌고 오르내림을 잠깐 색으로 보여 준다.
 * 휴대폰에서 줄을 누르면 종목 빠른 보기 시트, 넓은 화면은 종목 이름을 눌러 종목 화면으로
 */
export const HoldingsTable = memo(function HoldingsTable({
  positions,
  loading = false,
}: {
  positions: Position[];
  loading?: boolean;
}) {
  const cur = useCurrency();
  const { market } = useMarket();
  const quick = useCallback(
    (p: Position) => {
      if (isMobile()) openStockSheet({ symbol: p.symbol, name: p.name, market, position: p });
    },
    [market],
  );
  if (loading) return <SkeletonLines lines={4} label="보유 종목 불러오는 중" />;
  if (positions.length === 0)
    return (
      <div className="empty-action">
        <p className="muted empty">보유 종목이 없어요. 매니저가 기회를 찾는 중이에요 👀</p>
        <Link to="/room" className="btn tiny ghost">
          💬 트레이딩 룸에서 매니저 보기
        </Link>
      </div>
    );
  return (
    <table className="table holdings">
      <thead>
        <tr>
          <th>종목</th>
          <th className="r">수량</th>
          <th className="r">평단</th>
          <th className="r">현재가</th>
          <th className="r">평가금액</th>
          <th className="r">수익률</th>
          <th className="r">비중</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <HoldingRow key={p.symbol} p={p} cur={cur} onQuick={quick} />
        ))}
      </tbody>
    </table>
  );
});

/** 휴대폰에서는 이름을 눌러도 바로 이동하지 않고 줄 전체처럼 빠른 보기 시트를 연다 */
const stayOnMobile = (e: MouseEvent) => {
  if (isMobile()) e.preventDefault();
};

/** 한 줄 — 같은 종목·같은 값이면 다시 그리지 않는다 */
const HoldingRow = memo(function HoldingRow({
  p,
  cur,
  onQuick,
}: {
  p: Position;
  cur: Currency;
  onQuick: (p: Position) => void;
}) {
  return (
    <tr className="tap-row" onClick={() => onQuick(p)}>
      <td>
        <Link to={`/stock/${p.symbol}`} onClick={stayOnMobile}>
          {p.name || p.symbol}
        </Link>{" "}
        <small className="muted">{p.symbol}</small>
      </td>
      <td className="r">{fmtNum(p.quantity)}</td>
      <td className="r">{price(p.avg_price, cur)}</td>
      <td className="r">
        <AnimatedNumber value={p.last_price} format={PRICE_FMT[cur]} />
      </td>
      <td className="r">
        <AnimatedNumber value={p.market_value} format={MONEY_FMT[cur]} flash={false} />
      </td>
      <td className={`r ${signClass(p.pnl_pct)}`}>
        <AnimatedNumber value={p.pnl_pct} format={pctFmt} flash={false} />
      </td>
      <td className="r">{p.weight_pct.toFixed(1)}%</td>
    </tr>
  );
});
