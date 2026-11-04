"""리스크·전략·관심종목 API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.routes.common import C, U
from app.db.models import StrategyConfig, WatchlistItem
from app.risk.rules import RiskConfig
from app.strategies.rules import REGISTRY, build, param_schema

router = APIRouter()


# ====================================================================== 리스크/전략/관심종목
@router.get("/risk")
def get_risk(c: C, _: U) -> dict[str, Any]:
    return {"config": c.engine.risk.config.model_dump(), "schema": RiskConfig.model_json_schema()}


@router.put("/risk")
def put_risk(body: RiskConfig, c: C, _: U) -> dict[str, Any]:
    c.engine.set_risk_config(body)
    return {"config": body.model_dump()}


def _strategy_dict(r: StrategyConfig) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "type": r.type,
        "label": REGISTRY[r.type].label,
        "enabled": r.enabled,
        "params": r.params,
        "symbols": r.symbols,
        "schema": param_schema(r.type),
    }


@router.get("/strategies")
def strategies(c: C, _: U) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [_strategy_dict(r) for r in s.scalars(select(StrategyConfig).order_by(StrategyConfig.id))]


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    symbols: list[str] | None = None


@router.put("/strategies/{sid}")
def update_strategy(sid: int, body: StrategyUpdate, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        r = s.get(StrategyConfig, sid)
        if r is None:
            raise HTTPException(404, "전략 없음")
        if body.params is not None:
            try:
                build(r.type, body.params)
            except ValidationError as e:
                raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
            r.params = dict(build(r.type, body.params).params.model_dump())
        if body.enabled is not None:
            r.enabled = body.enabled
        if body.symbols is not None:
            r.symbols = [x.strip() for x in body.symbols if x.strip()]
        s.flush()
        out = _strategy_dict(r)
    c.hub.audit("CONFIG", f"전략 '{out['name']}' 변경", None, strategy=out)
    return out


@router.get("/watchlist")
def watchlist(c: C, _: U) -> list[dict[str, Any]]:
    with c.db.session() as s:
        rows = list(s.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at)))
    bars = c.store.last_bars(w.symbol for w in rows)  # 종목마다 따로 읽지 않는다 (B1)
    out = []
    for w in rows:
        b = bars.get(w.symbol)
        out.append(
            {
                "symbol": w.symbol,
                "name": w.name or (b.name if b else ""),
                "active": w.active,
                "close": b.close if b else None,
                "day": b.day.isoformat() if b else None,
            }
        )
    return out


class WatchBody(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,12}$")  # 미국 티커는 BRK.B 처럼 점이 있을 수 있다
    name: str = Field("", max_length=64)


@router.post("/watchlist")
async def add_watch(body: WatchBody, c: C, _: U) -> dict[str, Any]:
    if c.code == "US":
        body = WatchBody(symbol=body.symbol.upper(), name=body.name)
    name = body.name
    if not name:
        try:
            name = str((await c.toss.info([body.symbol])).get(body.symbol, {}).get("name", ""))
        except Exception:
            name = ""
    with c.db.session() as s:
        if s.get(WatchlistItem, body.symbol) is None:
            s.add(WatchlistItem(symbol=body.symbol, name=name or c.store.name_of(body.symbol)))
    return {"ok": True}


@router.delete("/watchlist/{symbol}")
def del_watch(symbol: str, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        w = s.get(WatchlistItem, symbol)
        if w:
            s.delete(w)
    return {"ok": True}
