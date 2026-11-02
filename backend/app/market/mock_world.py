"""키 없이 돌아가는 가상 시장.

- **가상 종목**(`MOCK01`~`MOCK20`, 실존 기업 아님)의 일봉·뉴스·재무제표를 시드 기반으로 결정적으로 생성한다.
- 실존 기업 이름을 쓰지 않는 이유: LLM 이 실제 기업에 대한 사전지식으로 판단하면 가상 데이터와 어긋난다(D-09).
- 이벤트(호재/악재)는 뉴스로 먼저 나오고, 그 뒤 며칠간 가격 drift 에 반영된다 → 리서치가 의미 있는 구조.
- 모든 조회는 `now` 시점까지 공개된 정보만 돌려준다(미래 정보 누설 방지).
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.core.clock import KST
from app.core.market_calendar import MarketCalendar
from app.core.ticks import round_to_tick
from app.market.types import Bar

START = date(2024, 1, 2)


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    sector: str
    market: str
    price: float  # 시작 가격
    shares: float  # 상장주식수
    revenue_q: float  # 분기 매출(원)
    margin: float  # 영업이익률
    growth: float  # 연 성장률
    vol: float  # 일 변동성


COMPANIES: list[Company] = [
    Company("MOCK01", "가온전자", "IT", "KOSPI", 71_000, 5.9e9, 70e12, 0.12, 0.06, 0.016),
    Company("MOCK02", "한빛반도체", "IT", "KOSPI", 182_000, 7.2e8, 15e12, 0.22, 0.15, 0.024),
    Company("MOCK03", "누리바이오", "헬스케어", "KOSDAQ", 48_500, 1.1e8, 2.1e11, 0.08, 0.25, 0.032),
    Company("MOCK04", "다온자동차", "경기소비재", "KOSPI", 231_000, 2.1e8, 40e12, 0.08, 0.04, 0.017),
    Company("MOCK05", "미래에너지", "에너지", "KOSPI", 132_500, 3.3e8, 9e12, 0.06, 0.03, 0.019),
    Company("MOCK06", "별빛게임즈", "커뮤니케이션", "KOSDAQ", 36_200, 6.6e7, 1.8e11, 0.18, 0.10, 0.030),
    Company("MOCK07", "새솔화학", "소재", "KOSPI", 318_000, 7.0e7, 12e12, 0.05, 0.02, 0.021),
    Company("MOCK08", "이음통신", "커뮤니케이션", "KOSPI", 52_400, 2.4e8, 4.5e12, 0.09, 0.01, 0.010),
    Company("MOCK09", "바른금융지주", "금융", "KOSPI", 61_300, 4.1e8, 3.2e12, 0.30, 0.03, 0.013),
    Company("MOCK10", "푸른식품", "필수소비재", "KOSPI", 287_000, 1.5e7, 2.6e12, 0.07, 0.03, 0.011),
    Company("MOCK11", "하늘항공", "산업재", "KOSPI", 24_150, 3.6e8, 4.0e12, 0.07, 0.05, 0.022),
    Company("MOCK12", "단비제약", "헬스케어", "KOSPI", 104_000, 1.2e8, 4.5e11, 0.11, 0.08, 0.020),
    Company("MOCK13", "솔빛배터리", "IT", "KOSDAQ", 214_500, 9.8e7, 1.9e12, 0.04, 0.20, 0.034),
    Company("MOCK14", "온새미로봇", "산업재", "KOSDAQ", 15_620, 8.1e7, 5.5e10, -0.05, 0.40, 0.041),
    Company("MOCK15", "마루건설", "산업재", "KOSPI", 30_850, 1.1e8, 3.3e12, 0.03, -0.02, 0.018),
    Company("MOCK16", "가람철강", "소재", "KOSPI", 388_500, 8.5e7, 19e12, 0.05, 0.00, 0.018),
    Company("MOCK17", "라온엔터", "커뮤니케이션", "KOSDAQ", 67_800, 4.2e7, 2.4e11, 0.14, 0.12, 0.029),
    Company("MOCK18", "소담유통", "필수소비재", "KOSPI", 71_900, 2.8e7, 3.8e12, 0.03, 0.01, 0.014),
    Company("MOCK19", "아라조선", "산업재", "KOSPI", 156_300, 7.0e7, 6.1e12, 0.04, 0.18, 0.027),
    Company("MOCK20", "여울플랫폼", "커뮤니케이션", "KOSPI", 182_400, 1.6e8, 2.5e12, 0.15, 0.09, 0.022),
]
BY_SYMBOL = {c.symbol: c for c in COMPANIES}

_POS_EVENTS = [
    (
        "대규모 공급계약 체결",
        "{name}이(가) 해외 고객사와 대규모 공급계약을 체결했다고 공시했다. 계약 규모는 최근 매출의 상당 부분에 해당한다.",
    ),
    (
        "분기 실적 시장 기대치 상회",
        "{name}의 잠정 실적이 컨센서스를 웃돌았다. 영업이익이 전년 대비 크게 늘었다.",
    ),
    (
        "신제품 호평에 증권가 목표가 상향",
        "증권가가 {name}의 목표주가를 잇달아 올렸다. 신제품 반응이 예상보다 좋다는 평가다.",
    ),
    ("자사주 매입·소각 결정", "{name}이(가) 주주환원 강화를 위해 자사주 매입 및 소각을 결정했다."),
    ("핵심 특허 승소", "{name}이(가) 경쟁사와의 특허 분쟁에서 승소했다."),
]
_NEG_EVENTS = [
    (
        "분기 실적 쇼크",
        "{name}의 잠정 영업이익이 시장 예상치를 크게 밑돌았다. 원가 부담이 커졌다는 분석이다.",
    ),
    ("제품 리콜 발표", "{name}이(가) 일부 제품의 결함으로 자발적 리콜을 실시한다고 밝혔다."),
    ("대주주 지분 대량 매도", "{name}의 대주주가 시간외 대량매매로 지분 일부를 처분했다."),
    ("소송 리스크 부각", "{name}을(를) 상대로 한 대규모 손해배상 소송이 제기됐다."),
    ("증권가 목표가 하향", "업황 둔화 우려로 증권가가 {name}의 목표주가를 낮췄다."),
]
_ROUTINE = [
    (
        "{sector} 업종 수급 동향 점검",
        "{sector} 업종 전반의 외국인·기관 수급이 엇갈리고 있다. {name}도 박스권 흐름을 이어가고 있다.",
    ),
    ("{name}, 주주총회 일정 공시", "{name}이(가) 정기 주주총회 일정을 공시했다."),
    ("{name} 임원 인사 단행", "{name}이(가) 일부 임원 인사를 단행했다."),
    ("{sector} 업황 전망 보고서", "증권가는 {sector} 업황이 당분간 완만한 흐름을 보일 것으로 전망했다."),
]


def _rng(*parts: object) -> random.Random:
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


@dataclass
class _Series:
    bars: list[Bar] = field(default_factory=list)
    index: dict[date, int] = field(default_factory=dict)
    events: dict[date, float] = field(default_factory=dict)  # 날짜 → 감성(-1~1)


class MockWorld:
    def __init__(self, seed: int, calendar: MarketCalendar) -> None:
        self.seed = seed
        self.cal = calendar
        self._series: dict[str, _Series] = {}

    # ------------------------------------------------------------------ 가격
    def _extend(self, c: Company, until: date) -> _Series:
        s = self._series.setdefault(c.symbol, _Series())
        last = s.bars[-1].day if s.bars else START - timedelta(days=1)
        if last >= until:
            return s
        prev = s.bars[-1].close if s.bars else c.price
        for d in self.cal.trading_days(last + timedelta(days=1), until):
            # 날짜별 독립 RNG → 생성 구간을 어떻게 나눠도 같은 결과(결정성)
            erng = _rng(self.seed, c.symbol, "ev", d)
            rng = _rng(self.seed, c.symbol, "px", d)
            gap_shock = 0.0
            if erng.random() < 0.035:
                senti = (1 if erng.random() < 0.5 else -1) * (0.5 + 0.5 * erng.random())
                s.events[d] = senti
                gap_shock = senti * c.vol * 1.2
            drift = 0.0
            for k in range(1, 6):  # 이벤트 후 5거래일간 drift
                j = len(s.bars) - k
                if j >= 0:
                    drift += s.events.get(s.bars[j].day, 0.0) * c.vol * 0.25
            mu = c.growth / 250
            gap = gap_shock + rng.gauss(0, c.vol * 0.3)
            ret = gap + mu + drift + rng.gauss(0, c.vol * 0.9)
            ret = max(-0.29, min(0.29, ret))  # 가격제한폭 ±30%
            open_ = round_to_tick(prev * (1 + gap))
            close = round_to_tick(prev * (1 + ret))
            hi = round_to_tick(max(open_, close) * (1 + abs(rng.gauss(0, c.vol * 0.4))), "up")
            lo = round_to_tick(min(open_, close) * (1 - abs(rng.gauss(0, c.vol * 0.4))), "down")
            base_vol = c.shares * 0.0025
            vol = float(int(base_vol * math.exp(rng.gauss(0, 0.35)) * (1 + 3 * abs(s.events.get(d, 0.0)))))
            bar = Bar(
                symbol=c.symbol,
                day=d,
                open=open_,
                high=hi,
                low=lo,
                close=close,
                volume=vol,
                value=vol * close,
                market_cap=close * c.shares,
                listed_shares=c.shares,
                name=c.name,
                market=c.market,
            )
            s.index[d] = len(s.bars)
            s.bars.append(bar)
            prev = close
        return s

    def bar(self, symbol: str, d: date) -> Bar | None:
        c = BY_SYMBOL.get(symbol)
        if c is None:
            return None
        s = self._extend(c, d)
        i = s.index.get(d)
        return s.bars[i] if i is not None else None

    def bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        c = BY_SYMBOL.get(symbol)
        if c is None:
            return []
        s = self._extend(c, end)
        return [b for b in s.bars if start <= b.day <= end]

    # ------------------------------------------------------------------ 뉴스
    def news(
        self, query: str, now: datetime, limit: int = 10, lookback_days: int = 14
    ) -> list[dict[str, Any]]:
        targets = [c for c in COMPANIES if c.symbol in query or c.name in query or c.sector in query]
        if not targets:
            targets = COMPANIES
        today = now.astimezone(KST).date()
        items: list[tuple[datetime, dict[str, Any]]] = []
        for c in targets:
            self._extend(c, today)
            s = self._series[c.symbol]
            for d in self.cal.trading_days(today - timedelta(days=lookback_days), today):
                senti = s.events.get(d)
                rng = _rng(self.seed, c.symbol, "news", d)
                if senti is not None:
                    tpl = (_POS_EVENTS if senti > 0 else _NEG_EVENTS)[rng.randrange(5)]
                    pub = datetime.combine(d, time(7, 30), KST)
                elif rng.random() < 0.12:
                    tpl = _ROUTINE[rng.randrange(len(_ROUTINE))]
                    pub = datetime.combine(d, time(rng.randrange(9, 18), rng.randrange(60)), KST)
                else:
                    continue
                if pub > now:
                    continue
                title = f"[{c.name}] " + tpl[0].format(name=c.name, sector=c.sector)
                items.append(
                    (
                        pub,
                        {
                            "title": title,
                            "description": tpl[1].format(name=c.name, sector=c.sector),
                            "link": f"mock://news/{c.symbol}/{d.isoformat()}",
                            "pubDate": pub.isoformat(),
                            "source": "MOCK",
                        },
                    )
                )
        items.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in items[:limit]]

    def disclosures(self, symbol: str, now: datetime, days: int = 30) -> list[dict[str, Any]]:
        c = BY_SYMBOL.get(symbol)
        if c is None:
            return []
        today = now.astimezone(KST).date()
        self._extend(c, today)
        out = []
        for d, senti in sorted(self._series[symbol].events.items(), reverse=True):
            if today - timedelta(days=days) <= d <= today and datetime.combine(d, time(7, 0), KST) <= now:
                kind = (
                    "단일판매ㆍ공급계약체결"
                    if senti > 0.7
                    else ("영업(잠정)실적(공정공시)" if abs(senti) > 0.6 else "기타 경영사항")
                )
                out.append(
                    {
                        "report_nm": kind,
                        "rcept_dt": d.strftime("%Y%m%d"),
                        "corp_name": c.name,
                        "rcept_no": f"MOCK{symbol}{d:%Y%m%d}",
                        "flr_nm": c.name,
                    }
                )
        return out

    # ------------------------------------------------------------------ 재무
    def financials(self, symbol: str, now: datetime) -> dict[str, Any]:
        """공개된(분기말+45일) 최근 4개 분기. DartClient.financials 와 같은 모양."""
        c = BY_SYMBOL.get(symbol)
        if c is None:
            return {"symbol": symbol, "available": False}
        today = now.astimezone(KST).date()
        quarters: list[dict[str, Any]] = []
        y, q = today.year, (today.month - 1) // 3  # 직전 분기부터 역순
        while len(quarters) < 5:
            if q == 0:
                y, q = y - 1, 4
            qend = date(y, q * 3, 28) + timedelta(days=4)
            qend = qend.replace(day=1) - timedelta(days=1)
            if qend + timedelta(days=45) <= today:
                quarters.append(self._quarter(c, y, q))
            q -= 1
        reports = [
            {
                **{k: v for k, v in qd.items() if k != "period"},
                "period": qd["period"],
                "type": "Q",
                "fs_div": "CFS",
            }
            for qd in quarters[:4]
        ]
        return {"symbol": symbol, "name": c.name, "available": True, "source": "MOCK", "reports": reports}

    def _quarter(self, c: Company, y: int, q: int) -> dict[str, Any]:
        rng = _rng(self.seed, c.symbol, "fin", y, q)
        t = (y - 2024) + (q - 1) / 4
        rev = c.revenue_q * (1 + c.growth) ** t * (1 + rng.gauss(0, 0.05)) * (1.05 if q == 4 else 1.0)
        margin = c.margin + rng.gauss(0, 0.02)
        op = rev * margin
        net = op * 0.75 + rev * rng.gauss(0, 0.01)
        dep = rev * (0.04 + 0.02 * rng.random())
        equity = c.shares * c.price * (0.6 + 0.3 * rng.random()) * (1 + c.growth) ** t
        liab = equity * (0.5 + rng.random())
        return {
            "period": f"{y}Q{q}",
            "revenue": rev,
            "operating_income": op,
            "net_income": net,
            "depreciation": dep,
            "operating_cash_flow": op + dep + rev * rng.gauss(0, 0.02),
            "capex": -rev * (0.05 + 0.04 * rng.random()),
            "total_assets": equity + liab,
            "total_liabilities": liab,
            "total_equity": equity,
        }


class MockDataProvider:
    """MockWorld 를 MarketDataProvider 로 노출. 공개 시각(T+1 13:00) 규칙을 지킨다."""

    name = "mock"

    def __init__(self, world: MockWorld, clock: Any) -> None:
        self.world = world
        self.clock = clock

    def _cutoff(self) -> date:
        return self.world.cal.latest_published_day(self.clock.now())

    async def fetch_day(self, day: date) -> list[Bar]:
        if day > self._cutoff():
            return []
        out = [self.world.bar(c.symbol, day) for c in COMPANIES]
        return [b for b in out if b is not None]

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return self.world.bars(symbol, start, min(end, self._cutoff()))
