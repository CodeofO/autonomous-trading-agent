"""장외 주문 설정 (D-57): 프리마켓·애프터마켓(미장은 데이마켓도)에서 주문을 받을지 시장마다 켜고 끈다.

- 세션 시각은 토스 장 운영 캘린더가 날짜마다 준 값을 그대로 쓴다(국장 KRX+NXT 통합, 미장 서머타임 반영).
  캘린더를 못 받은 날은 장외 세션이 없는 것으로 보고 주문을 받지 않는다.
- 저장: 시장 kv `extended_hours` = {세션: bool}. 기본은 모두 끔(정규장만).
- 켜면: 그 세션 안에서도 판단 주기대로 매니저가 판단하고(D-61, scheduler.ext_session_times), 리스크 엔진이
  주문을 받는다(`fill_basis` = EXT). 모의계좌는 시세 시각이 그 세션 안일 때만 현재가로 체결, 세션 끝에 미체결분
  취소. 실거래는 토스로 바로 주문이 나간다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import KST
from app.core.market_calendar import EXT_LABEL, MarketCalendar
from app.engine.settings_store import SettingsStore

KEY = "extended_hours"

HINT = {
    "day_market": "한국 낮 시간 미국 주식 거래 (토스증권 데이마켓)",
    "pre_market": "정규장 시작 전 장외 거래",
    "after_market": "정규장 마감 뒤 장외 거래",
}
KR_HINT = {
    "pre_market": "NXT 프리마켓 (정규장 시작 전)",
    "after_market": "NXT 애프터마켓 (정규장 마감 뒤)",
}


def load(kv: SettingsStore, cal: MarketCalendar) -> dict[str, bool]:
    saved = kv.get(KEY)
    return {k: bool(saved.get(k, False)) for k in cal.EXT_SESSIONS}


def apply(kv: SettingsStore, cal: MarketCalendar) -> dict[str, bool]:
    """저장된 설정을 캘린더에 반영 (주문 접수 규칙이 바로 바뀐다)."""
    cfg = load(kv, cal)
    cal.extended_enabled = frozenset(k for k, on in cfg.items() if on)
    return cfg


def save(kv: SettingsStore, cal: MarketCalendar, changes: dict[str, bool]) -> dict[str, bool]:
    unknown = set(changes) - set(cal.EXT_SESSIONS)
    if unknown:
        raise ValueError(f"이 시장에 없는 장외 세션이에요: {', '.join(sorted(unknown))}")
    cfg = {**load(kv, cal), **{k: bool(v) for k, v in changes.items()}}
    kv.set(KEY, cfg)
    apply(kv, cal)
    return cfg


def view(cal: MarketCalendar, now: datetime, *, market: str, live: bool) -> dict[str, Any]:
    """설정 화면용: 세션마다 켬/끔, 다음(또는 지금) 세션 시각(한국 시각·현지 시각), 지금 진행 중인지."""
    known = cal.extended_sessions()
    hints = KR_HINT if market == "KR" else HINT
    rows = []
    for name in cal.EXT_SESSIONS:
        nxt = next((x for x in known if x.name == name and x.end > now), None)
        rows.append(
            {
                "key": name,
                "label": EXT_LABEL[name],
                "hint": hints.get(name, ""),
                "enabled": name in cal.extended_enabled,
                "active": nxt is not None and nxt.start <= now,
                "next": (
                    {
                        "day": nxt.day.isoformat(),
                        "start": nxt.start.astimezone(KST).isoformat(),
                        "end": nxt.end.astimezone(KST).isoformat(),
                        "start_local": f"{nxt.start.astimezone(cal.tz):%H:%M}",
                        "end_local": f"{nxt.end.astimezone(cal.tz):%H:%M}",
                    }
                    if nxt is not None
                    else None
                ),
            }
        )
    return {
        "market": market,
        "timezone": str(cal.tz),
        "live": live,
        "calendar_known": bool(known),  # 토스 장 운영 캘린더에서 장외 시각을 받았는지
        "sessions": rows,
        "order_hours": cal.order_hours_text(),
    }


def describe(cfg: dict[str, bool]) -> str:
    on = [EXT_LABEL[k] for k, v in cfg.items() if v]
    return f"장외 {'·'.join(on)} 주문 켬" if on else "장외 주문 끔 (정규장만)"
