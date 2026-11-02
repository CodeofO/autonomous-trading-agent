"""app_settings 테이블 키-값 접근."""

from __future__ import annotations

from typing import Any

from app.db.models import AppSetting
from app.db.session import Database


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(AppSetting, key)
            return dict(row.value) if row else dict(default or {})

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self.db.session() as s:
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=value))
            else:
                row.value = dict(value)
