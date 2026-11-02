"""트레이딩 룸 활동 표시: 도구별 데이터 출처, 결과 한 줄 요약, 펼쳐 볼 핵심값."""

from __future__ import annotations

from typing import Any

from app.agent.tools import TERMINAL_TOOL

# 도구 → (분류, 데이터 출처)
TOOL_GROUPS: dict[str, tuple[str, str]] = {
    "get_portfolio": ("portfolio", "모의계좌"),
    "get_trade_history": ("portfolio", "모의계좌"),
    "screen_stocks": ("market", "토스증권"),
    "get_price_history": ("market", "토스증권"),
    "get_market_overview": ("market", "토스증권"),
    "explore_sector": ("market", "토스증권"),
    "get_supply_demand": ("market", "토스증권"),
    "get_stock_status": ("market", "토스증권"),
    "get_financials": ("research", "DART"),
    "get_disclosures": ("research", "DART"),
    "search_news": ("research", "NAVER"),
    TERMINAL_TOOL: ("decision", "모의계좌"),
}


def tool_source(name: str, live: bool = False) -> str:
    source = TOOL_GROUPS.get(name, ("market", "토스증권"))[1]
    if source == "모의계좌":
        return "토스 실계좌" if live else "모의계좌"
    return source


def _won(v: Any) -> str:
    return f"{float(v):,.0f}원" if isinstance(v, int | float) else "—"


def _pct(v: Any) -> str | None:
    return f"{float(v):+.1f}%" if isinstance(v, int | float) else None


def _qty(v: Any) -> str:
    return f"{float(v):+,.0f}" if isinstance(v, int | float) else "—"


def _cut(text: Any, n: int = 38) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def result_line(name: str, r: dict[str, Any]) -> str:
    """도구 결과를 채팅 칩 옆에 붙일 한 줄로 요약한다 (도구가 돌려준 값만 사용)."""
    if "error" in r:
        return f"실패: {_cut(r['error'], 80)}"
    parts: list[str] = []
    if name == "get_portfolio":
        parts = [
            f"평가금 {_won(r.get('equity'))}",
            f"현금 {_won(r.get('cash'))}",
            f"보유 {len(r.get('positions', []))}종목",
        ]
    elif name == "get_trade_history":
        parts = [f"체결 {r.get('fill_count', 0)}건", f"실현손익 {_won(r.get('realized_pnl_total'))}"]
    elif name == "screen_stocks":
        rows = r.get("results", [])
        parts = [f"후보 {r.get('count', len(rows))}개"]
        if rows:
            parts.append(", ".join(str(x.get("name") or x.get("symbol")) for x in rows[:3]))
    elif name == "get_price_history":
        price = r.get("realtime_price") or (r.get("last") or {}).get("close")
        parts = [f"{'현재가' if r.get('realtime_price') else '종가'} {_won(price)}"]
        if (p := _pct((r.get("returns_pct") or {}).get("20d"))) is not None:
            parts.append(f"20일 {p}")
        if r.get("rsi14") is not None:
            parts.append(f"RSI {r['rsi14']}")
    elif name == "get_financials":
        if not r.get("available", True) or not r.get("reports"):
            return _cut(r.get("reason") or "재무 자료 없음", 80)
        r0, val = r["reports"][0], r.get("valuation") or {}
        parts = [str(r0.get("period") or "")]
        if r0.get("op_margin_pct") is not None:
            parts.append(f"영업이익률 {r0['op_margin_pct']}%")
        if val.get("per") is not None:
            parts.append(f"PER {val['per']}")
        if val.get("roe_pct") is not None:
            parts.append(f"ROE {val['roe_pct']}%")
    elif name == "get_disclosures":
        items = r.get("items", [])
        parts = [f"공시 {r.get('count', len(items))}건"]
        if items:
            parts.append(f"「{_cut(items[0].get('report_nm'), 30)}」")
    elif name == "search_news":
        items = r.get("items", [])
        parts = [f"뉴스 {r.get('count', len(items))}건"]
        if items:
            parts.append(f"「{_cut(items[0].get('title'))}」")
    elif name == "get_market_overview":
        top = [str(s.get("name")) for s in (r.get("top_sectors_1d") or [])[:2]]
        if top:
            parts.append("강한 섹터 " + "·".join(top))
        fx = (r.get("usd_krw") or {}).get("rate")
        if fx:
            parts.append(f"원/달러 {float(fx):,.1f}")
    elif name == "get_supply_demand":
        sums = next((v for k, v in r.items() if k.startswith("investor_net_buy_sum_")), {}) or {}
        parts = [f"외국인 {_qty(sums.get('foreigner'))}주", f"기관 {_qty(sums.get('institution'))}주"]
    elif name == "get_stock_status":
        if r.get("trading_suspended"):
            parts.append("거래정지")
        warns = r.get("warnings") or []
        parts.append(f"유의사항 {len(warns)}건" if warns else "유의사항 없음")
        if r.get("spread_pct") is not None:
            parts.append(f"스프레드 {r['spread_pct']}%")
    elif name == "explore_sector":
        if sec := r.get("sector"):
            parts = [str(sec.get("name")), f"종목 {len(r.get('stocks', []))}개"]
        else:
            parts = ["오늘 강한 섹터 " + "·".join(str(s.get("name")) for s in (r.get("top_1d") or [])[:3])]
    return " · ".join(p for p in parts if p) or "완료"


def preview(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """칩을 펼쳤을 때 보여줄 핵심값 (원본 결과 전체는 LLM 에게만 전달)."""
    if "error" in result:
        return {"error": result["error"]}
    if name == "search_news":
        return {
            "titles": [i.get("title", "")[:60] for i in result.get("items", [])[:3]],
            "count": result.get("count"),
        }
    if name == "get_price_history":
        return {
            "close": (result.get("last") or {}).get("close"),
            "realtime_price": result.get("realtime_price"),
            "returns_pct": result.get("returns_pct"),
            "rsi14": result.get("rsi14"),
            "ma": result.get("ma"),
        }
    if name == "get_financials":
        r0 = (result.get("reports") or [{}])[0]
        return {
            "period": r0.get("period"),
            "revenue": r0.get("revenue"),
            "op_margin_pct": r0.get("op_margin_pct"),
            **{k: (result.get("valuation") or {}).get(k) for k in ("per", "pbr", "roe_pct")},
        }
    if name == "screen_stocks":
        return {"top": [f"{r['name']}" for r in result.get("results", [])[:5]], "as_of": result.get("as_of")}
    if name == "get_disclosures":
        return {"items": [i.get("report_nm") for i in result.get("items", [])[:3]]}
    if name == "get_portfolio":
        return {
            "equity": result.get("equity"),
            "cash": result.get("cash"),
            "positions": len(result.get("positions", [])),
        }
    if name == "get_trade_history":
        return {
            "realized_pnl_total": result.get("realized_pnl_total"),
            "fill_count": result.get("fill_count"),
            "by_stock": result.get("realized_pnl_by_stock"),
        }
    if name == "get_supply_demand":
        return {k: v for k, v in result.items() if k.startswith("investor_net_buy_sum_")}
    if name == "get_stock_status":
        return {
            k: result.get(k) for k in ("warnings", "trading_suspended", "spread_pct", "bid_ask_volume_ratio")
        }
    if name == "get_market_overview":
        return {
            "top_sectors_1d": [s.get("name") for s in (result.get("top_sectors_1d") or [])[:5]],
            "usd_krw": result.get("usd_krw"),
        }
    if name == "explore_sector":
        sec = result.get("sector") or {}
        return {
            "sector": sec.get("name"),
            "stocks": [s.get("name") for s in (result.get("stocks") or [])[:5]],
        }
    return {}
