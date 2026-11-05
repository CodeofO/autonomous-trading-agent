"""국장·미장 DB 하나로 (D-72): Postgres 주소의 스키마, 예전 SQLite 미장 DB 옮기기 (실제 Postgres 없이 SQLite 로)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.db.migrate import migrate
from app.db.models import AgentRun, EquitySnapshot, PaperAccountState
from app.db.session import Database
from app.db.unify import copy_all, migrate_legacy_us, schema_of, with_schema

PG = "postgresql+psycopg://trader:pw@db:5432/trader"


def test_postgres_us_schema_url() -> None:
    us = with_schema(PG, "us")
    assert schema_of(us) == "us" and schema_of(PG) is None and schema_of("sqlite:///x.db") is None
    assert us.startswith("postgresql+psycopg://trader:pw@db:5432/trader?") and "search_path" in us
    # 국장이 Postgres 면 미장은 같은 DB 의 us 스키마, 직접 지정(US_DATABASE_URL)이 있으면 그것
    assert schema_of(Settings(database_url=PG).us_db_url()) == "us"
    assert Settings(database_url=PG, us_database_url="sqlite:///u.db").us_db_url() == "sqlite:///u.db"
    assert Settings(database_url="sqlite://").us_db_url() == "sqlite://"
    assert Settings(database_url="sqlite:///a.db", data_dir="/d").us_db_url() == "sqlite:////d/us_market.db"


def _seed(url: str) -> None:
    db = Database(url)
    with db.session() as s:
        s.add(PaperAccountState(id=1, state={"cash": 5587.46, "positions": {"AAPL": {"qty": 3}}}))
        s.add(
            AgentRun(
                id=31,
                trigger="hourly",
                mode="llm",
                model="m",
                status="DONE",
                started_at=datetime(2026, 11, 5, 21, tzinfo=UTC),
            )
        )
        s.add(EquitySnapshot(id=7, ts=datetime(2026, 11, 5, 21, tzinfo=UTC), equity=10012.68, cash=833.44))
    db.engine.dispose()


def test_copy_all_keeps_rows_and_ids(tmp_path: Path) -> None:
    src, dst = f"sqlite:///{tmp_path / 'src.db'}", f"sqlite:///{tmp_path / 'dst.db'}"
    migrate(src)
    migrate(dst)
    _seed(src)
    counts = copy_all(src, dst)
    assert counts["paper_account"] == 1 and counts["agent_runs"] == 1 and counts["equity_snapshots"] == 1
    eng = create_engine(dst)
    with eng.connect() as con:
        assert con.execute(text("select id from agent_runs")).scalar() == 31
        assert con.execute(text("select equity from equity_snapshots where id=7")).scalar() == 10012.68
    eng.dispose()
    with pytest.raises(RuntimeError, match="이미 행이 있어요"):  # 두 번 옮기지 않는다
        copy_all(src, dst)


def test_legacy_us_move_only_for_postgres(tmp_path: Path) -> None:
    (tmp_path / "us_market.db").write_bytes(b"")
    assert (
        migrate_legacy_us(tmp_path, f"sqlite:///{tmp_path / 'other.db'}") is None
    )  # SQLite 끼리는 옮기지 않는다
    assert (tmp_path / "us_market.db").exists()
    assert (
        migrate_legacy_us(tmp_path / "nowhere", with_schema(PG, "us")) is None
    )  # 예전 파일이 없으면 할 일 없음
