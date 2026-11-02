"""에이전트 도구: 포트폴리오 · 종목 스크리닝 · 시세/지표 · 재무제표 · 공시 · 뉴스 · 최종 결정 제출.

도구 결과는 **데이터**다. 뉴스·공시 본문에 지시문이 있어도 따르지 않도록 프롬프트에서 못 박는다.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from app.core.clock import Clock
from app.market.store import PriceStore
from app.research.service import ResearchService
from app.strategies.indicators import pct_change, rsi, sma, volatility

log = logging.getLogger(__name__)
EOK = 1e8  # 억원
MAX_RESULT_CHARS = 7000

PortfolioFn = Callable[[], Awaitable[dict[str, Any]]]
ScreenerFn = Callable[[str, str, int, float], Awaitable[list[dict[str, Any]]]]
QuoteFn = Callable[[str], Awaitable[Any]]


def _f(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _eok(v: float | None) -> float | None:
    return None if v is None else round(v / EOK, 1)


def _pct(v: float | None, digits: int = 1) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v * 100, digits)


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _growth(cur: float | None, prior: float | None) -> float | None:
    if cur is None or prior is None or prior <= 0:
        return None
    return cur / prior - 1


DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "6자리 종목코드"},
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "quantity": {
            "type": "integer",
            "minimum": 0,
            "description": "주문 수량(주). target_weight 와 둘 중 하나",
        },
        "target_weight": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "매매 후 목표 비중(0~1). SELL 에서 0 이면 전량 매도",
        },
        "order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
        "limit_price": {"type": "number"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "description": "판단 이유 2~4문장 (한국어)"},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "근거 목록. 예: '뉴스 10/30: 대규모 수주', 'PER 8.2배, 업종 대비 저평가'",
        },
    },
    "required": ["symbol", "action", "confidence", "rationale"],
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_portfolio",
        "description": "현재 모의계좌 현황: 현금, 총자산, 보유종목(평단·수익률·비중·보유일), 미체결 주문, "
        "리스크 한도, 지금 주문하면 어떤 가격(시가/종가)으로 체결되는지.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_trade_history",
        "description": "매매 내역: 최근 체결(매수·매도, 사유, 판단 세션)과 매도별·종목별·전체 실현손익. "
        "'왜 팔았는지', '실현손익이 어디서 났는지' 확인용.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}},
        },
    },
    {
        "name": "screen_stocks",
        "description": "종목 스크리닝(후보 발굴용). 토스 연결 시 시장 전체 실시간 랭킹 상위 100 기준, 아니면 최신 일봉 기준.",
        "parameters": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "market_cap",
                        "trading_value",
                        "volume",
                        "change_1d",
                        "losers_1d",
                        "change_1w",
                        "change_1mo",
                        "foreigner_net_buy",
                        "institution_net_buy",
                    ],
                    "default": "trading_value",
                    "description": "market_cap=시가총액, trading_value=거래대금, volume=거래량, change_*=상승률, losers_1d=하락률",
                },
                "market": {"type": "string", "enum": ["ALL", "KOSPI", "KOSDAQ"], "default": "ALL"},
                "min_market_cap_eok": {"type": "number", "description": "최소 시가총액(억원)", "default": 0},
                "descending": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 15},
            },
        },
    },
    {
        "name": "get_price_history",
        "description": "종목의 일봉 요약과 기술 지표(이동평균, RSI, 수익률, 변동성, 52주 고저).",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "minimum": 20, "maximum": 250, "default": 120},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_financials",
        "description": "재무제표 요약(매출·영업이익·순이익·감가상각비·영업현금흐름·CAPEX·자산·부채·자본)과 "
        "파생 지표(이익률, 부채비율, ROE, PER, PBR, 전년 동기 대비 증감). 금액 단위 억원.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_disclosures",
        "description": "최근 공시 목록(DART).",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": 180, "default": 30},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "search_news",
        "description": "뉴스 검색(최신순). 종목명·업종·거시 키워드로 검색.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "submit_decisions",
        "description": "최종 판단 제출(세션 종료). 보유 종목은 전부 포함해야 한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "market_view": {"type": "string", "description": "시장 전반에 대한 한두 문장 관점"},
                "summary": {
                    "type": "string",
                    "description": "이번 세션 결론 요약 (채팅에 표시됨, 친근한 말투)",
                },
                "decisions": {"type": "array", "items": DECISION_SCHEMA},
            },
            "required": ["summary", "decisions"],
        },
    },
]

TERMINAL_TOOL = "submit_decisions"

# 토스증권 연결 시에만 노출되는 도구 (조회 전용)
TOSS_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_market_overview",
        "description": "시장 전체 상황(토스): KOSPI·KOSDAQ 지수와 기간 수익률, 지수별 투자자(개인·외국인·기관) 순매수, "
        "상승률 상위 섹터, 원/달러 환율, 외국인·기관 순매수/순매도 상위 종목.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_supply_demand",
        "description": "종목 수급(토스): 투자자별 순매수(외국인·기관·개인), 프로그램매매, 공매도 비중, 신용잔고율, 대차잔고 추이.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "minimum": 3, "maximum": 30, "default": 10},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_stock_status",
        "description": "종목 상태(토스): 매수 유의사항(과열·투자경고·VI 등), 상·하한가, 호가 5단계·스프레드·잔량 불균형, "
        "최근 체결 흐름, 상장·거래정지 정보. 매수 전 리스크 점검용.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "explore_sector",
        "description": "섹터 탐색(토스): 섹터 이름으로 찾아 기간별 등락률과 구성 종목(시가총액·거래대금)을 본다. "
        "query 를 비우면 오늘/이번 주 상승률 상위 섹터를 보여준다.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "예: 반도체, 2차전지, 조선"}},
        },
    },
]


def openai_tools(toss: bool = False) -> list[dict[str, Any]]:
    specs = (
        TOOL_SPECS[:-1] + (TOSS_TOOL_SPECS if toss else []) + TOOL_SPECS[-1:]
    )  # submit_decisions 는 마지막
    return [{"type": "function", "function": spec} for spec in specs]


def tool_label(name: str, args: dict[str, Any], names: Callable[[str], str]) -> str:
    sym = str(args.get("symbol", ""))
    who = f"{names(sym) or sym}" if sym else ""
    return {
        "get_portfolio": "💼 포트폴리오 확인",
        "get_trade_history": "🧾 매매 내역·실현손익 확인",
        "screen_stocks": f"🧭 종목 스크리닝 ({args.get('sort_by', 'market_cap')})",
        "get_price_history": f"📈 {who} 차트·지표 확인",
        "get_financials": f"🧾 {who} 재무제표 분석",
        "get_disclosures": f"📑 {who} 공시 확인",
        "search_news": f"📰 뉴스 검색: “{args.get('query', '')}”",
        "submit_decisions": "✅ 최종 판단 제출",
        "get_market_overview": "🌐 시장 전체 흐름 확인 (지수·수급·섹터·환율)",
        "get_supply_demand": f"🧲 {who} 수급 확인 (외국인·기관·공매도)",
        "get_stock_status": f"🚦 {who} 상태 점검 (유의사항·호가)",
        "explore_sector": f"🗂️ 섹터 탐색: “{args.get('query') or '상승 상위'}”",
    }.get(name, f"🔧 {name}")


class ToolBox:
    def __init__(
        self,
        portfolio: PortfolioFn,
        store: PriceStore,
        research: ResearchService,
        clock: Clock,
        *,
        screener: ScreenerFn | None = None,
        quote: QuoteFn | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.store = store
        self.research = research
        self.clock = clock
        self.screener = screener  # 토스 랭킹 기반 실시간 스크리닝
        self.quote = quote  # 토스 실시간 현재가
        self.toss: Any = None  # TossMarketProvider (컨테이너에서 연결)
        self.trades: Callable[[int], dict[str, Any]] | None = None  # 매매 원장 (컨테이너에서 연결)

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"t_{name}", None)
        if fn is None or name == TERMINAL_TOOL:
            return {"error": f"알 수 없는 도구: {name}"}
        try:
            result: dict[str, Any] = await fn(**{k: v for k, v in args.items() if not k.startswith("_")})
            return result
        except TypeError as e:
            return {"error": f"인자 오류: {e}"}
        except Exception as e:  # 도구 실패는 에이전트에게 알리고 계속 진행
            return {"error": f"{type(e).__name__}: {str(e)[:200]}"}

    @staticmethod
    def dumps(result: dict[str, Any]) -> str:
        s = json.dumps(result, ensure_ascii=False, default=str)
        return s if len(s) <= MAX_RESULT_CHARS else s[:MAX_RESULT_CHARS] + "…(생략)"

    # ------------------------------------------------------------------ 도구 구현
    async def t_get_portfolio(self) -> dict[str, Any]:
        return await self.portfolio()

    async def t_get_trade_history(self, limit: int = 20) -> dict[str, Any]:
        if self.trades is None:
            return {"error": "매매 내역을 볼 수 없어요"}
        return self.trades(max(1, min(int(limit), 50)))

    async def t_screen_stocks(
        self,
        sort_by: str = "market_cap",
        market: str = "ALL",
        min_market_cap_eok: float = 0,
        descending: bool = True,
        limit: int = 15,
    ) -> dict[str, Any]:
        if self.screener is not None:
            ranked = await self.screener(sort_by, market, int(limit), float(min_market_cap_eok) * EOK)
            return {
                "as_of": self.clock.now().isoformat(),
                "source": "토스증권 실시간 랭킹(시장 전체)",
                "count": len(ranked),
                "results": ranked,
            }
        snap = self.store.snapshot()
        if not snap:
            return {"error": "저장된 시세가 없습니다"}
        day = snap[0].day
        prev_day = self.store.history(snap[0].symbol, until=day - timedelta(days=1), n=1)
        prev = {b.symbol: b.close for b in self.store.snapshot(prev_day[0].day)} if prev_day else {}
        rows: list[dict[str, Any]] = []
        for b in snap:
            if market != "ALL" and b.market != market:
                continue
            if b.market_cap < min_market_cap_eok * EOK:
                continue
            chg = (b.close / prev[b.symbol] - 1) if prev.get(b.symbol) else None
            rows.append(
                {
                    "symbol": b.symbol,
                    "name": b.name,
                    "market": b.market,
                    "close": b.close,
                    "change_1d_pct": _pct(chg, 2),
                    "market_cap_eok": _eok(b.market_cap),
                    "trading_value_eok": _eok(b.value),
                }
            )
        key = {
            "market_cap": "market_cap_eok",
            "trading_value": "trading_value_eok",
            "change_1d": "change_1d_pct",
        }.get(sort_by, "market_cap_eok")
        rows.sort(key=lambda r: (r[key] is None, -float(r[key] or 0) if descending else float(r[key] or 0)))
        return {"as_of": day.isoformat(), "count": len(rows), "results": rows[: max(1, min(30, int(limit)))]}

    async def t_get_price_history(self, symbol: str, days: int = 120) -> dict[str, Any]:
        latest = self.clock.now().date() if self.quote is not None else self.store.latest_day()
        if latest is None:
            return {"error": "저장된 시세가 없습니다"}
        days = max(20, min(250, int(days)))
        await self.store.ensure_history(symbol, latest, int(days * 1.5) + 10)
        bars = self.store.history(symbol, latest, max(days, 252))
        if not bars:
            return {"error": f"{symbol} 시세 없음"}
        closes = [b.close for b in bars]
        recent = bars[-days:]
        vals20 = [b.value for b in bars[-20:]]
        step = max(1, math.ceil(len(recent) / 40))  # 토큰 절약: 최대 40개 표본
        sampled = recent[::-1][::step][::-1]
        if sampled[-1] is not recent[-1]:
            sampled.append(recent[-1])
        v20 = volatility(closes, 20)
        live: dict[str, Any] = {}
        if self.quote is not None:
            q = await self.quote(symbol)
            if q is not None:
                live = {
                    "realtime_price": q.price,
                    "realtime_vs_prev_close_pct": _pct(q.price / closes[-2] - 1)
                    if len(closes) >= 2 and bars[-1].day == latest
                    else _pct(q.price / closes[-1] - 1),
                }
        return {
            **live,
            "symbol": symbol,
            "name": bars[-1].name,
            "as_of": bars[-1].day.isoformat(),
            "last": {
                "open": bars[-1].open,
                "high": bars[-1].high,
                "low": bars[-1].low,
                "close": bars[-1].close,
                "volume": bars[-1].volume,
            },
            "returns_pct": {f"{n}d": _pct(pct_change(closes, n)) for n in (1, 5, 20, 60, 120)},
            "ma": {f"ma{n}": (round(v) if (v := sma(closes, n)) else None) for n in (5, 20, 60, 120)},
            "rsi14": round(r, 1) if (r := rsi(closes, 14)) is not None else None,
            "volatility_20d_annual_pct": _pct(v20 * math.sqrt(250) if v20 is not None else None),
            "high_52w": max(b.high for b in bars[-250:]),
            "low_52w": min(b.low for b in bars[-250:]),
            "avg_trading_value_20d_eok": _eok(sum(vals20) / len(vals20)) if vals20 else None,
            "market_cap_eok": _eok(bars[-1].market_cap),
            "closes_sampled": [[b.day.isoformat(), b.close] for b in sampled],
        }

    async def t_get_financials(self, symbol: str) -> dict[str, Any]:
        fin = await self.research.financials(symbol)
        if not fin.get("available"):
            return fin
        mcap = await self._market_cap(symbol)
        reports = fin["reports"]
        out_reports = []
        for r in reports:
            out_reports.append(
                {
                    "period": r["period"],
                    "type": r.get("type"),
                    "fs_div": r.get("fs_div"),
                    **{
                        k: _eok(r.get(k))
                        for k in (
                            "revenue",
                            "operating_income",
                            "net_income",
                            "depreciation",
                            "operating_cash_flow",
                            "capex",
                            "total_assets",
                            "total_liabilities",
                            "total_equity",
                        )
                    },
                    "op_margin_pct": _pct(_ratio(r.get("operating_income"), r.get("revenue"))),
                    "net_margin_pct": _pct(_ratio(r.get("net_income"), r.get("revenue"))),
                    "debt_ratio_pct": _pct(_ratio(r.get("total_liabilities"), r.get("total_equity")), 0),
                    "ebitda": _eok((r.get("operating_income") or 0) + (r.get("depreciation") or 0))
                    if r.get("operating_income") is not None
                    else None,
                    "yoy_revenue_pct": _pct(_growth(r.get("revenue"), (r.get("prior") or {}).get("revenue"))),
                    "yoy_op_income_pct": _pct(
                        _growth(r.get("operating_income"), (r.get("prior") or {}).get("operating_income"))
                    ),
                }
            )
        # 연환산 순이익: 분기 4개가 있으면 합, 사업보고서가 있으면 그것, 아니면 최신 분기×4
        q = [r for r in reports if r.get("type") == "Q" and r.get("net_income") is not None]
        a = [r for r in reports if r.get("type") == "A" and r.get("net_income") is not None]
        if len(q) >= 4:
            earnings, basis = sum(r["net_income"] for r in q[:4]), "최근 4분기 합"
        elif a:
            earnings, basis = a[0]["net_income"], f"{a[0]['period']}"
        elif q:
            earnings, basis = q[0]["net_income"] * 4, "최근 분기×4 (단순 연환산)"
        else:
            earnings, basis = None, "없음"
        equity = reports[0].get("total_equity")
        valuation = {
            "market_cap_eok": _eok(mcap),
            "per": round(mcap / earnings, 1) if mcap and earnings and earnings > 0 else None,
            "pbr": round(mcap / equity, 2) if mcap and equity and equity > 0 else None,
            "roe_pct": _pct(_ratio(earnings, equity)),
            "earnings_basis": basis,
        }
        return {
            "symbol": symbol,
            "source": fin.get("source"),
            "unit": "억원",
            "reports": out_reports,
            "valuation": valuation,
        }

    async def _market_cap(self, symbol: str) -> float | None:
        """시가총액: 토스 현재가 × 발행주식수 (없으면 저장된 일봉 기준)."""
        if self.toss is not None:
            try:
                shares = _f((await self.toss.info([symbol])).get(symbol, {}).get("sharesOutstanding"))
                q = await self.quote(symbol) if self.quote is not None else None
                if shares and q is not None and q.price:
                    return float(q.price * shares)
            except Exception as e:  # 토스 일시 장애 → 일봉으로 대체
                log.warning("시가총액 계산 실패 %s: %s", symbol, e)
        last = self.store.last_bar(symbol)
        return last.market_cap if last and last.market_cap else None

    async def t_get_disclosures(self, symbol: str, days: int = 30) -> dict[str, Any]:
        items = await self.research.disclosures(symbol, max(1, min(180, int(days))))
        return {"symbol": symbol, "count": len(items), "items": items[:20]}

    # ------------------------------------------------------------------ 토스 전용 도구
    def _need_toss(self) -> Any:
        if self.toss is None:
            raise RuntimeError("토스증권 연결이 없어 사용할 수 없습니다")
        return self.toss

    async def _names(self, symbols: list[str]) -> dict[str, str]:
        uni = await self._need_toss().universe()
        return {s: str(uni.get(s, {}).get("name", "")) for s in symbols}

    async def t_get_market_overview(self) -> dict[str, Any]:
        tc = self._need_toss().client
        out: dict[str, Any] = {"as_of": self.clock.now().isoformat()}
        idx = {}
        for it in await tc.index_prices():
            sym = it["symbol"]
            closes = [_f(c.get("closePrice")) for c in reversed(await tc.index_candles(sym, 61))]
            inv = await tc.index_investor_trading(sym, 3)
            idx[sym] = {
                "last": _f(it.get("lastPrice")),
                "returns_pct": {f"{n}d": _pct(pct_change(closes, n)) for n in (1, 5, 20, 60)},
                "investor_net_buy_eok_last3d": [
                    {
                        "date": r.get("date"),
                        **{
                            k: _eok(
                                _f((r.get(k) or {}).get("buyAmount")) - _f((r.get(k) or {}).get("sellAmount"))
                            )
                            for k in ("individual", "foreigner", "institution")
                            if r.get(k)
                        },
                    }
                    for r in inv
                ],
            }
        out["indices"] = idx
        out["top_sectors_1d"] = [
            {"name": r["name"], "change_pct": _pct(_f(r.get("changeRate")))}
            for r in (await tc.sector_rankings("TOP_GAINERS", "1d"))[:8]
        ]
        out["top_sectors_1w"] = [
            {"name": r["name"], "change_pct": _pct(_f(r.get("changeRate")))}
            for r in (await tc.sector_rankings("TOP_GAINERS", "1w"))[:5]
        ]
        fx = await tc.exchange_rate()
        out["usd_krw"] = {"rate": _f(fx.get("rate")), "direction": fx.get("rateChangeType")}
        inv_rank = await tc.investor_rankings(10)
        syms = sorted(
            {
                str(x["symbol"])
                for grp in inv_rank.values()
                if isinstance(grp, dict)
                for key in ("netBuyRankings", "netSellRankings")
                for x in grp.get(key, [])
            }
        )
        names = await self._names(syms)
        out["investor_rankings"] = {
            who: {
                "net_buy_top": [
                    f"{names.get(str(x['symbol'])) or x['symbol']}({_eok(_f(x.get('netBuyAmount')))}억)"
                    for x in grp.get("netBuyRankings", [])[:10]
                ],
                "net_sell_top": [
                    f"{names.get(str(x['symbol'])) or x['symbol']}({_eok(_f(x.get('netSellAmount')))}억)"
                    for x in grp.get("netSellRankings", [])[:10]
                ],
            }
            for who, grp in inv_rank.items()
            if who in ("foreigner", "institution") and isinstance(grp, dict)
        }
        return out

    async def t_get_supply_demand(self, symbol: str, days: int = 10) -> dict[str, Any]:
        tc = self._need_toss().client
        n = max(3, min(30, int(days)))
        inv = await tc.stock_trend(symbol, "investor-trading", n)
        prog = await tc.stock_trend(symbol, "program-trades", min(n, 10))
        short = await tc.stock_trend(symbol, "short-selling", min(n, 10))
        credit = await tc.stock_trend(symbol, "credit-trades", 5)
        lend = await tc.stock_trend(symbol, "securities-lending", 5)

        def net(r: dict[str, Any], k: str) -> float | None:
            v = r.get(k)
            return _f(v.get("netBuyVolume")) if isinstance(v, dict) else None

        daily = [
            {
                "date": r.get("date"),
                "foreigner": net(r, "foreigner"),
                "institution": net(r, "institution"),
                "individual": net(r, "individual"),
            }
            for r in inv
        ]
        sums = {
            k: sum(d[k] for d in daily if d[k] is not None)
            for k in ("foreigner", "institution", "individual")
        }
        return {
            "symbol": symbol,
            "unit": "주(순매수 수량)",
            "investor_net_buy_daily": daily,
            f"investor_net_buy_sum_{len(daily)}d": sums,
            "program_net_buy": [
                {
                    "date": r.get("date"),
                    "arbitrage": _f((r.get("arbitrage") or {}).get("netBuyVolume")),
                    "non_arbitrage": _f((r.get("nonArbitrage") or {}).get("netBuyVolume")),
                }
                for r in prog[:5]
            ],
            "short_selling_volume_rate_pct": [
                {"date": r.get("date"), "rate": _pct(_f(r.get("shortSellingVolumeRate")), 2)}
                for r in short[:5]
            ],
            "margin_loan_balance_rate_pct": [
                {"date": r.get("date"), "rate": _pct(_f((r.get("marginLoan") or {}).get("balanceRate")), 2)}
                for r in credit
            ],
            "lending_balance_qty": [
                {"date": r.get("date"), "balance": _f(r.get("balanceQuantity"))} for r in lend
            ],
        }

    async def t_get_stock_status(self, symbol: str) -> dict[str, Any]:
        tp = self._need_toss()
        tc = tp.client
        info = (await tp.info([symbol])).get(symbol, {})
        ob = await tc.orderbook(symbol)
        asks = [(_f(a.get("price")), _f(a.get("volume"))) for a in (ob.get("asks") or [])[:5]]
        bids = [(_f(b.get("price")), _f(b.get("volume"))) for b in (ob.get("bids") or [])[:5]]
        best_ask = min((p for p, _ in asks), default=None)
        best_bid = max((p for p, _ in bids), default=None)
        ask_vol, bid_vol = sum(v for _, v in asks), sum(v for _, v in bids)
        trades = await tc.trades(symbol, 30)
        tvol = sum(_f(t.get("volume")) for t in trades)
        vwap = sum(_f(t.get("price")) * _f(t.get("volume")) for t in trades) / tvol if tvol else None
        lim = await tc.price_limits(symbol)
        km = info.get("koreanMarketDetail") or {}
        return {
            "symbol": symbol,
            "name": info.get("name"),
            "market": info.get("market"),
            "list_date": info.get("listDate"),
            "warnings": await tc.warnings(symbol),
            "trading_suspended": bool(km.get("krxTradingSuspended") or km.get("nxtTradingSuspended")),
            "liquidation_trading": km.get("liquidationTrading"),
            "price_limits": {
                "upper": _f(lim.get("upperLimitPrice")),
                "lower": _f(lim.get("lowerLimitPrice")),
            },
            "orderbook_top5": {"asks": asks, "bids": bids},
            "spread_pct": _pct((best_ask - best_bid) / best_bid, 3) if best_ask and best_bid else None,
            "bid_ask_volume_ratio": round(bid_vol / ask_vol, 2) if ask_vol else None,
            "recent_trades": {"count": len(trades), "vwap": round(vwap, 1) if vwap else None, "volume": tvol},
        }

    async def t_explore_sector(self, query: str = "") -> dict[str, Any]:
        tc = self._need_toss().client
        if not query.strip():
            return {
                "top_1d": [
                    {
                        "sector_id": r["sectorId"],
                        "name": r["name"],
                        "change_pct": _pct(_f(r.get("changeRate"))),
                    }
                    for r in (await tc.sector_rankings("TOP_GAINERS", "1d"))[:10]
                ],
                "top_1w": [
                    {
                        "sector_id": r["sectorId"],
                        "name": r["name"],
                        "change_pct": _pct(_f(r.get("changeRate"))),
                    }
                    for r in (await tc.sector_rankings("TOP_GAINERS", "1w"))[:10]
                ],
            }
        sectors = await tc.sectors()
        q = query.strip()
        matches = [s for s in sectors if q in str(s.get("name", ""))] or [
            s for s in sectors if any(w and w in str(s.get("name", "")) for w in q.split())
        ]
        if not matches:
            return {
                "query": q,
                "error": "일치하는 섹터가 없습니다",
                "examples": [s["name"] for s in sectors if s.get("depth") == 0][:20],
            }
        top = sorted(matches, key=lambda s: (s.get("depth", 9), len(str(s.get("name", "")))))[0]
        detail = await tc.sector(str(top["sectorId"]))
        stocks = await tc.sector_stocks(str(top["sectorId"]))
        stocks.sort(key=lambda s: -_f(s.get("marketCapitalization")))
        perf = {
            p.get("duration"): _pct(_f(p.get("changeRate"))) if p.get("changeRate") is not None else None
            for p in ((detail.get("performance") or {}).get("changeRates") or [])
        }
        return {
            "sector": {
                "id": top["sectorId"],
                "name": top["name"],
                "summary": detail.get("summary"),
                "change_pct": perf,
            },
            "other_matches": [s["name"] for s in matches if s is not top][:8],
            "stocks": [
                {
                    "symbol": s["symbol"],
                    "name": s.get("name"),
                    "market_cap_eok": _eok(_f(s.get("marketCapitalization"))),
                    "trading_value_eok": _eok(_f(s.get("tradingAmount"))),
                }
                for s in stocks[:15]
            ],
        }

    async def t_search_news(self, query: str, limit: int = 8) -> dict[str, Any]:
        items = await self.research.news(str(query)[:60], max(1, min(20, int(limit))))
        return {
            "query": query,
            "count": len(items),
            "items": [{k: it.get(k) for k in ("title", "description", "pubDate", "link")} for it in items],
        }
