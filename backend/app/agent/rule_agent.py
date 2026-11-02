"""LLM 이 설정되지 않았을 때 쓰는 규칙 기반 대체 에이전트.

같은 도구(ToolBox)를 쓰고 같은 결정 형식을 낸다. 판단 로직은 단순한 예시이며 투자 권유가 아니다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.tools import ToolBox
from app.market.types import Bar
from app.strategies.rules import Strategy

POS_WORDS = ("상회", "수주", "공급계약", "상향", "자사주", "승소", "호평", "개선", "흑자")
NEG_WORDS = ("쇼크", "부진", "리콜", "소송", "하향", "대량 매도", "지분 대량", "적자", "하회")

BUY_THRESHOLD = 1

Say = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def news_score(items: list[dict[str, Any]]) -> tuple[int, list[str]]:
    score, ev = 0, []
    for it in items[:8]:
        t = f"{it.get('title', '')} {it.get('description', '')}"
        if any(w in t for w in POS_WORDS):
            score += 1
            ev.append(f"호재 뉴스: {it.get('title', '')[:40]}")
        elif any(w in t for w in NEG_WORDS):
            score -= 1
            ev.append(f"악재 뉴스: {it.get('title', '')[:40]}")
    return score, ev


async def rule_based_decisions(
    tb: ToolBox, strategies: list[Strategy], say: Say, max_candidates: int = 12
) -> dict[str, Any]:
    await say(
        "text",
        "LLM 이 설정되지 않아서 오늘은 규칙 기반으로 판단할게요 📏 (뉴스 키워드 + 기술적 신호 + 재무)",
        {},
    )
    await say("tool", "💼 포트폴리오 확인", {"tool": "get_portfolio"})
    pf = await tb.call("get_portfolio", {})
    held = {p["symbol"]: p for p in pf.get("positions", [])}
    await say("tool", "🧭 종목 스크리닝 (market_cap)", {"tool": "screen_stocks"})
    scr = await tb.call("screen_stocks", {"sort_by": "market_cap", "limit": max_candidates})
    candidates = [r["symbol"] for r in scr.get("results", [])]
    decisions: list[dict[str, Any]] = []
    for sym in list(held) + [c for c in candidates if c not in held]:
        name = held.get(sym, {}).get("name") or next(
            (r["name"] for r in scr.get("results", []) if r["symbol"] == sym), sym
        )
        px = await tb.call("get_price_history", {"symbol": sym, "days": 120})  # 과거 시세 확보 포함
        bars: list[Bar] = tb.store.history(sym, n=130)
        tech = [s.evaluate(bars) for s in strategies]
        tech_sig = [t for t in tech if t is not None]
        news = await tb.call("search_news", {"query": name, "limit": 6})
        nscore, nev = news_score(news.get("items", []))
        fin = await tb.call("get_financials", {"symbol": sym})
        val = fin.get("valuation", {}) if isinstance(fin, dict) else {}
        per = val.get("per")
        evidence = [f"{t.reason}" for t in tech_sig] + nev[:2]
        if per:
            evidence.append(f"PER {per}배")
        score = nscore + sum(1 if t.action == "BUY" else -1 for t in tech_sig)
        r20 = (px.get("returns_pct") or {}).get("20d") if isinstance(px, dict) else None
        if r20 is not None:
            if r20 >= 5:
                score += 1
                evidence.append(f"20일 수익률 +{r20}% (추세 양호)")
            elif r20 <= -10:
                score -= 1
                evidence.append(f"20일 수익률 {r20}% (추세 약함)")
        if per is not None and per > 40:
            score -= 1
        await say(
            "tool",
            f"🔎 {name}: 점수 {score:+d}" + (f" — {evidence[0]}" if evidence else ""),
            {"tool": "rule_score", "symbol": sym, "preview": {"score": score, "evidence": evidence[:3]}},
        )
        if sym in held:
            p = held[sym]
            if score <= -1:
                decisions.append(
                    {
                        "symbol": sym,
                        "action": "SELL",
                        "target_weight": 0,
                        "confidence": 0.55,
                        "rationale": f"악재/약세 신호 합계 {score}점으로 보유 논리가 약해졌다고 봤어요.",
                        "evidence": evidence,
                    }
                )
            else:
                decisions.append(
                    {
                        "symbol": sym,
                        "action": "HOLD",
                        "confidence": 0.5,
                        "rationale": f"뚜렷한 매도 신호가 없어 유지해요 (점수 {score}, 수익률 {p.get('pnl_pct')}%).",
                        "evidence": evidence,
                    }
                )
        elif score >= BUY_THRESHOLD:
            decisions.append(
                {
                    "symbol": sym,
                    "action": "BUY",
                    "target_weight": 0.1,
                    "confidence": min(0.9, 0.4 + 0.1 * score),
                    "rationale": f"긍정 신호 합계 {score}점이라 10% 비중으로 담아볼게요.",
                    "evidence": evidence,
                }
            )
    buys = [d for d in decisions if d["action"] == "BUY"]
    if len(buys) > 3:  # 하루 신규 매수는 상위 3개만
        keep = {id(d) for d in sorted(buys, key=lambda d: -d["confidence"])[:3]}
        decisions = [d for d in decisions if d["action"] != "BUY" or id(d) in keep]
    n_buy = sum(d["action"] == "BUY" for d in decisions)
    n_sell = sum(d["action"] == "SELL" for d in decisions)
    return {
        "market_view": "규칙 기반 판단(LLM 미설정)이라 시장 전망은 생략해요.",
        "summary": f"규칙 점검 끝! 매수 {n_buy}건, 매도 {n_sell}건, 나머지는 유지예요.",
        "decisions": decisions,
    }
