"""Alembic 마이그레이션을 코드에서 실행한다 (국장·미장·전략 시나리오 DB 공통)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import BACKEND_DIR


def migrate(url: str) -> None:
    """파일 DB 를 최신 스키마로 (없으면 만든다)."""
    from alembic import command
    from alembic.config import Config

    from app.db.unify import ensure_schema

    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(url)  # Postgres `us` 스키마 (D-72)
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def in_memory(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:")
