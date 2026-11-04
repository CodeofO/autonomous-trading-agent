"""시장 프로필 (국장 KR / 미장 US, D-48).

시장마다 다른 값(통화·시간대·캘린더·유니버스·랭킹 국가·가격 단위·비용·초기 자금·국내 전용 도구 여부)을
한곳에 모아, 엔진·브로커·도구·스케줄러가 if 를 흩뿌리지 않고 이 값을 받아 쓴다.
KR 프로필은 기존 동작을 그대로 재현한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.core.clock import KST
from app.core.config import Settings
from app.core.market_calendar import MarketCalendar
from app.core.us_calendar import NY, UsMarketCalendar

MarketCode = Literal["KR", "US"]
MARKETS: tuple[MarketCode, ...] = ("KR", "US")

# 미장 기본 관심종목 (관심종목이 비어 있을 때만 넣는다)
US_DEFAULT_WATCHLIST: tuple[tuple[str, str], ...] = (
    ("AAPL", "애플"),
    ("MSFT", "마이크로소프트"),
    ("NVDA", "엔비디아"),
    ("AMZN", "아마존닷컴"),
    ("GOOGL", "알파벳 A"),
    ("META", "메타 플랫폼스"),
    ("TSLA", "테슬라"),
    ("QQQ", "인베스코 QQQ"),
    ("SPY", "SPDR S&P500"),
)


@dataclass(frozen=True)
class MarketSpec:
    code: MarketCode
    name: str  # 국장 / 미장
    currency: str  # KRW / USD
    tz: ZoneInfo
    universe_markets: tuple[str, ...]  # stocks/all 의 market 값
    security_types: tuple[str, ...]  # 유니버스에 넣을 종목 유형
    ranking_country: str  # rankings·sectors 의 marketCountry
    calendar_api: str  # TossClient 의 장 운영 캘린더 메서드 이름
    health_symbol: str  # 토스 접근 확인용 종목
    kr_tools: bool  # 국내 전용 도구(수급·DART·국내 지수 개요) 사용
    initial_cash: float
    commission_rate: float
    sell_tax_rate: float
    slippage_bps: float
    max_volume_participation: float
    live: bool  # 토스증권 실계좌로 주문 (KR: TRADING_MODE, US: US_TRADING_MODE — 사용자가 .env 에서 켠다)
    default_watchlist: tuple[tuple[str, str], ...] = field(default=())

    # ------------------------------------------------------------ 시간
    def calendar(self) -> MarketCalendar:
        # 미장 실거래는 장 시작 전 주문을 보내지 않는다: 토스 미장은 데이·프리마켓이 따로 있어
        # 장전에 낸 주문이 어느 세션에서 체결되는지 명세에 없다 (D-55)
        if self.code == "US":
            return UsMarketCalendar(pre_open=not self.live)
        return MarketCalendar()

    def local_date(self, t: datetime) -> date:
        return t.astimezone(self.tz).date()

    # ------------------------------------------------------------ 금액·가격
    @property
    def is_usd(self) -> bool:
        return self.currency == "USD"

    def round_price(self, px: float) -> float:
        """체결가 반올림. KR 은 기존과 같이 소수 둘째 자리, US 는 $1 이상 0.01·$1 미만 0.0001 (미국 호가 단위)."""
        if self.is_usd:
            return round(px, 2) if px >= 1 else round(px, 4)
        return round(px, 2)

    def money(self, v: float) -> float:
        """화면·도구용 금액 반올림: KRW 는 원 단위 정수, USD 는 센트."""
        return round(v, 2) if self.is_usd else round(v)

    def fmt(self, v: float) -> str:
        """채팅·알림 문구용 금액 표기."""
        if self.is_usd:
            return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"
        return f"{v:,.0f}원"

    def fmt_signed(self, v: float) -> str:
        if self.is_usd:
            return ("+" if v >= 0 else "-") + f"${abs(v):,.2f}"
        return f"{v:+,.0f}원"

    def fmt_price(self, px: float) -> str:
        if self.is_usd:
            return f"${px:,.2f}" if px >= 1 else f"${px:.4f}"
        return f"{px:,.0f}원"


def spec_for(code: str, settings: Settings) -> MarketSpec:
    if code == "US":
        return MarketSpec(
            code="US",
            name="미장",
            currency="USD",
            tz=NY,
            universe_markets=("NASDAQ", "NYSE", "AMEX"),
            security_types=("STOCK", "ETF"),
            ranking_country="US",
            calendar_api="market_calendar_us",
            health_symbol="AAPL",
            kr_tools=False,
            initial_cash=settings.paper_us_initial_cash,
            commission_rate=settings.paper_us_commission_rate,
            sell_tax_rate=0.0,  # 국내 증권거래세 없음. SEC·FINRA TAF 수수료는 반영하지 않음 (D-48)
            slippage_bps=settings.paper_us_slippage_bps,
            max_volume_participation=settings.paper_max_volume_participation,
            live=settings.us_is_live,
            default_watchlist=US_DEFAULT_WATCHLIST,
        )
    return MarketSpec(
        code="KR",
        name="국장",
        currency="KRW",
        tz=KST,
        universe_markets=("KOSPI", "KOSDAQ"),
        security_types=("STOCK",),
        ranking_country="KR",
        calendar_api="market_calendar_kr",
        health_symbol="005930",
        kr_tools=True,
        initial_cash=settings.paper_initial_cash,
        commission_rate=settings.paper_commission_rate,
        sell_tax_rate=settings.paper_sell_tax_rate,
        slippage_bps=settings.paper_slippage_bps,
        max_volume_participation=settings.paper_max_volume_participation,
        live=settings.is_live,
    )
