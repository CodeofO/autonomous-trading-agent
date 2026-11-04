"""하루 한 번 데이터 정리 (B7). 사용자가 보는 기록(주문·체결·세션·결정·채팅·감사 로그·자산 곡선)은 지우지 않는다.

지우는 것은 다시 받을 수 있는 캐시와 중복 정보뿐이다.
- api_cache: 수명이 지난 항목만. 수명은 읽는 쪽(`DartClient._statement`)의 규칙과 같다.
  - 빈 결과(`{"rows": []}`, '아직 공시 안 됨')는 1일 동안만 유효 → 1일이 지나면 삭제
  - 재무제표 행이 있는 항목은 만료가 없다. 다만 `financials()` 는 올해·작년·재작년 보고서만 찾으므로
    그보다 오래된 연도의 키는 다시 읽힐 일이 없다 → 1년 여유를 두고(올해 − 3년 미만) 삭제
- 모의계좌 JSON(paper_account.state): 끝난 주문을 최근 N건만 남긴다(`PaperBroker.prune_closed_orders`).
  주문 기록 전체는 orders 표에 그대로 있다.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, delete, func, select

from app.db.models import ApiCache
from app.db.session import Database

log = logging.getLogger(__name__)

EMPTY_TTL = timedelta(days=1)  # dart.py `_statement`: 빈 결과는 하루 동안만 캐시로 본다
EMPTY_MAX_LEN = 32  # '{"rows": []}' (12자) — 행이 하나라도 있으면 수백 자
FS_KEY = re.compile(r"^dart:fs:[^:]+:(\d{4}):")  # dart:fs:{corp}:{year}:{reprt}:{fs_div}
FS_YEARS_KEPT = 3  # financials() 가 보는 연도(올해·작년·재작년) + 1년 여유


def prune_api_cache(db: Database, now: datetime) -> int:
    """수명이 지난 api_cache 항목을 지운다. 지운 행 수."""
    now = now.astimezone(UTC)
    n = 0
    with db.session() as s:
        res = s.execute(
            delete(ApiCache)
            .where(
                ApiCache.key.like("dart:fs:%"),
                ApiCache.fetched_at < now - EMPTY_TTL,
                func.length(cast(ApiCache.payload, String)) <= EMPTY_MAX_LEN,
            )
            .execution_options(synchronize_session=False)
        )
        n += int(getattr(res, "rowcount", 0) or 0)
        oldest = now.year - FS_YEARS_KEPT
        stale = [
            k
            for k in s.scalars(select(ApiCache.key).where(ApiCache.key.like("dart:fs:%")))
            if (m := FS_KEY.match(k)) and int(m.group(1)) < oldest
        ]
        for i in range(0, len(stale), 500):
            res = s.execute(
                delete(ApiCache)
                .where(ApiCache.key.in_(stale[i : i + 500]))
                .execution_options(synchronize_session=False)
            )
            n += int(getattr(res, "rowcount", 0) or 0)
    return n


def run_maintenance(db: Database, now: datetime, broker: Any = None) -> dict[str, int]:
    """한 스택(국장 또는 미장 DB)의 정리 작업. 실패해도 예외를 밖으로 내지 않는다(기동·스케줄을 막지 않게)."""
    out = {"api_cache": 0, "paper_orders": 0}
    try:
        out["api_cache"] = prune_api_cache(db, now)
    except Exception:
        log.exception("api_cache 정리 실패")
    prune = getattr(broker, "prune_closed_orders", None)
    if prune is not None and getattr(broker, "persist", False):
        try:
            out["paper_orders"] = int(prune())
            if out["paper_orders"]:
                broker._save()
        except Exception:
            log.exception("모의계좌 주문 기록 정리 실패")
    if any(out.values()):
        log.info("데이터 정리: %s", out)
    return out
