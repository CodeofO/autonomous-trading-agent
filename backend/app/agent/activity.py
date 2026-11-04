"""트레이딩 룸 활동 표시: 도구별 데이터 출처, 결과 한 줄 요약, 펼쳐 볼 핵심값."""

from __future__ import annotations

from collections.abc import Callable
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
    "search_web": ("research", "GOOGLE"),
    TERMINAL_TOOL: ("decision", "모의계좌"),
}


def tool_source(name: str, live: bool = False) -> str:
    source = TOOL_GROUPS.get(name, ("market", "토스증권"))[1]
    if source == "모의계좌":
        return "토스 실계좌" if live else "모의계좌"
    return source


def _won(v: Any, currency: Any = None) -> str:
    """금액 표기. 결과에 currency=USD 가 있으면(미장, D-48) 달러로."""
    if not isinstance(v, int | float):
        return "—"
    return f"${float(v):,.2f}" if currency == "USD" else f"{float(v):,.0f}원"


def _pct(v: Any) -> str | None:
    return f"{float(v):+.1f}%" if isinstance(v, int | float) else None


def _qty(v: Any) -> str:
    return f"{float(v):+,.0f}" if isinstance(v, int | float) else "—"


def _cut(text: Any, n: int = 38) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------- 결과 한 줄 (도구별)
# 각 함수는 한 줄을 이루는 조각 목록을 돌려준다. 문자열을 돌려주면 그대로 한 줄이 된다(조각을 잇지 않음).
Line = list[str] | str


def _line_portfolio(r: dict[str, Any], cur: Any) -> Line:
    return [
        f"평가금 {_won(r.get('equity'), cur)}",
        f"현금 {_won(r.get('cash'), cur)}",
        f"보유 {len(r.get('positions', []))}종목",
    ]


def _line_trade_history(r: dict[str, Any], cur: Any) -> Line:
    return [f"체결 {r.get('fill_count', 0)}건", f"실현손익 {_won(r.get('realized_pnl_total'), cur)}"]


def _line_screen(r: dict[str, Any], cur: Any) -> Line:
    rows = r.get("results", [])
    parts = [f"후보 {r.get('count', len(rows))}개"]
    if rows:
        parts.append(", ".join(str(x.get("name") or x.get("symbol")) for x in rows[:3]))
    return parts


def _line_price_history(r: dict[str, Any], cur: Any) -> Line:
    price = r.get("realtime_price") or (r.get("last") or {}).get("close")
    parts = [f"{'현재가' if r.get('realtime_price') else '종가'} {_won(price, cur)}"]
    if (p := _pct((r.get("returns_pct") or {}).get("20d"))) is not None:
        parts.append(f"20일 {p}")
    if r.get("rsi14") is not None:
        parts.append(f"RSI {r['rsi14']}")
    return parts


def _line_financials(r: dict[str, Any], cur: Any) -> Line:
    if not r.get("available", True) or not r.get("reports"):
        return _cut(r.get("reason") or "재무 자료 없음", 80)
    r0, val = r["reports"][0], r.get("valuation") or {}
    optional = (
        (r0.get("op_margin_pct"), "영업이익률 {}%"),
        (val.get("per"), "PER {}"),
        (val.get("roe_pct"), "ROE {}%"),
    )
    return [str(r0.get("period") or "")] + [fmt.format(v) for v, fmt in optional if v is not None]


def _line_items(label: str, title_key: str, n: int) -> Callable[[dict[str, Any], Any], Line]:
    """공시·뉴스: '{label} N건' + 첫 항목 제목."""

    def line(r: dict[str, Any], cur: Any) -> Line:
        items = r.get("items", [])
        parts = [f"{label} {r.get('count', len(items))}건"]
        if items:
            parts.append(f"「{_cut(items[0].get(title_key), n)}」")
        return parts

    return line


def _line_web(r: dict[str, Any], cur: Any) -> Line:
    # 구글 검색 결과(제목·링크)는 화면에 그대로 보이지 않는다 (약관: 결과를 보여 주려면 Search Suggestions 필요)
    return [f"결과 {r.get('count', 0)}건"]


def _line_market_overview(r: dict[str, Any], cur: Any) -> Line:
    parts = []
    top = [str(s.get("name")) for s in (r.get("top_sectors_1d") or [])[:2]]
    if top:
        parts.append("강한 섹터 " + "·".join(top))
    fx = (r.get("usd_krw") or {}).get("rate")
    if fx:
        parts.append(f"원/달러 {float(fx):,.1f}")
    return parts


def _line_supply(r: dict[str, Any], cur: Any) -> Line:
    sums = next((v for k, v in r.items() if k.startswith("investor_net_buy_sum_")), {}) or {}
    return [f"외국인 {_qty(sums.get('foreigner'))}주", f"기관 {_qty(sums.get('institution'))}주"]


def _line_status(r: dict[str, Any], cur: Any) -> Line:
    parts = ["거래정지"] if r.get("trading_suspended") else []
    warns = r.get("warnings") or []
    parts.append(f"유의사항 {len(warns)}건" if warns else "유의사항 없음")
    if r.get("spread_pct") is not None:
        parts.append(f"스프레드 {r['spread_pct']}%")
    return parts


def _line_sector(r: dict[str, Any], cur: Any) -> Line:
    if sec := r.get("sector"):
        return [str(sec.get("name")), f"종목 {len(r.get('stocks', []))}개"]
    return ["오늘 강한 섹터 " + "·".join(str(s.get("name")) for s in (r.get("top_1d") or [])[:3])]


RESULT_LINES: dict[str, Callable[[dict[str, Any], Any], Line]] = {
    "get_portfolio": _line_portfolio,
    "get_trade_history": _line_trade_history,
    "screen_stocks": _line_screen,
    "get_price_history": _line_price_history,
    "get_financials": _line_financials,
    "get_disclosures": _line_items("공시", "report_nm", 30),
    "search_news": _line_items("뉴스", "title", 38),
    "search_web": _line_web,
    "get_market_overview": _line_market_overview,
    "get_supply_demand": _line_supply,
    "get_stock_status": _line_status,
    "explore_sector": _line_sector,
}


def result_line(name: str, r: dict[str, Any]) -> str:
    """도구 결과를 채팅 칩 옆에 붙일 한 줄로 요약한다 (도구가 돌려준 값만 사용)."""
    if "error" in r:
        return f"실패: {_cut(r['error'], 80)}"
    fn = RESULT_LINES.get(name)
    parts = fn(r, r.get("currency")) if fn else []
    if isinstance(parts, str):
        return parts
    return " · ".join(p for p in parts if p) or "완료"


# ---------------------------------------------------------------------- 펼쳐 볼 핵심값 (도구별)
def _pick(*keys: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda r: {k: r.get(k) for k in keys}


def _pv_financials(r: dict[str, Any]) -> dict[str, Any]:
    r0 = (r.get("reports") or [{}])[0]
    return {
        "period": r0.get("period"),
        "revenue": r0.get("revenue"),
        "op_margin_pct": r0.get("op_margin_pct"),
        **{k: (r.get("valuation") or {}).get(k) for k in ("per", "pbr", "roe_pct")},
    }


PREVIEWS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "search_news": lambda r: {
        "titles": [i.get("title", "")[:60] for i in r.get("items", [])[:3]],
        "count": r.get("count"),
    },
    "search_web": _pick("count", "grounded"),  # 건수만 (제목·링크는 매니저에게만)
    "get_price_history": lambda r: {
        "close": (r.get("last") or {}).get("close"),
        **_pick("realtime_price", "returns_pct", "rsi14", "ma")(r),
    },
    "get_financials": _pv_financials,
    "screen_stocks": lambda r: {
        "top": [f"{x['name']}" for x in r.get("results", [])[:5]],
        "as_of": r.get("as_of"),
    },
    "get_disclosures": lambda r: {"items": [i.get("report_nm") for i in r.get("items", [])[:3]]},
    "get_portfolio": lambda r: {
        "equity": r.get("equity"),
        "cash": r.get("cash"),
        "positions": len(r.get("positions", [])),
    },
    "get_trade_history": lambda r: {
        "realized_pnl_total": r.get("realized_pnl_total"),
        "fill_count": r.get("fill_count"),
        "by_stock": r.get("realized_pnl_by_stock"),
    },
    "get_supply_demand": lambda r: {k: v for k, v in r.items() if k.startswith("investor_net_buy_sum_")},
    "get_stock_status": _pick("warnings", "trading_suspended", "spread_pct", "bid_ask_volume_ratio"),
    "get_market_overview": lambda r: {
        "top_sectors_1d": [s.get("name") for s in (r.get("top_sectors_1d") or [])[:5]],
        "usd_krw": r.get("usd_krw"),
    },
    "explore_sector": lambda r: {
        "sector": (r.get("sector") or {}).get("name"),
        "stocks": [s.get("name") for s in (r.get("stocks") or [])[:5]],
    },
}


def preview(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """칩을 펼쳤을 때 보여줄 핵심값 (원본 결과 전체는 LLM 에게만 전달)."""
    if "error" in result:
        return {"error": result["error"]}
    fn = PREVIEWS.get(name)
    return fn(result) if fn else {}
