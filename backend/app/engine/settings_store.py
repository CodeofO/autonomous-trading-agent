"""app_settings 테이블 키-값 접근.

읽기는 메모리 캐시로 처리한다(B3). 프로세스 하나가 DB 를 쓰므로, 쓰기(`set`)가 DB 커밋 뒤 캐시를 바로 고친다
(write-through) — 엔진 상태·킬 스위치·리스크 설정은 쓰고 나서 바로 읽어도 새 값이다.
캐시는 DB(Database 인스턴스)마다 하나다: 국장·미장 kv 는 서로 다른 DB 라 따로, 같은 DB 에 SettingsStore 를
여러 개 만들어도(테스트 등) 같은 캐시를 같이 쓴다.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import select

from app.db.models import AppSetting
from app.db.session import Database


class _KvCache:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.values: dict[str, dict[str, Any]] | None = None  # None = 아직 읽지 않음


_CACHES: WeakKeyDictionary[Database, _KvCache] = WeakKeyDictionary()
_CACHES_LOCK = threading.Lock()


def _cache_for(db: Database) -> _KvCache:
    with _CACHES_LOCK:
        cache = _CACHES.get(db)
        if cache is None:
            cache = _CACHES[db] = _KvCache()
        return cache


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache = _cache_for(db)

    def _values(self) -> dict[str, dict[str, Any]]:
        """캐시된 전체 키-값 (처음 한 번 표 전체를 읽는다 — 행이 수십 개뿐). 호출자가 잠금을 잡고 있어야 한다."""
        if self._cache.values is None:
            with self.db.session() as s:
                self._cache.values = {r.key: r.value for r in s.scalars(select(AppSetting))}
        return self._cache.values

    def get(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._cache.lock:
            values = self._values()
            if key in values:
                # 깊은 복사: 호출자가 고쳐도 캐시는 그대로 (DB 에서 새로 읽은 것과 같은 동작)
                return dict(copy.deepcopy(values[key]))
        return dict(default or {})

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._cache.lock:
            with self.db.session() as s:
                row = s.get(AppSetting, key)
                if row is None:
                    s.add(AppSetting(key=key, value=value))
                else:
                    row.value = dict(value)
            # 커밋이 끝난 뒤에만 캐시를 고친다. JSON 왕복 = DB 에 저장됐다가 읽힌 모양 그대로
            if self._cache.values is not None:
                self._cache.values[key] = json.loads(json.dumps(value))

    def invalidate(self) -> None:
        """다음 읽기 때 DB 에서 다시 읽는다 (이 저장소를 거치지 않고 표를 직접 고친 경우용)."""
        with self._cache.lock:
            self._cache.values = None
