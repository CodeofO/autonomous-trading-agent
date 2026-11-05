"""국장·미장을 Postgres 데이터베이스 하나로 (D-72).

국장 DATABASE_URL 이 Postgres 면 미장은 같은 데이터베이스의 `us` 스키마를 쓴다(테이블 구조는 그대로, 스키마로 나눔).
연결 주소에 libpq `options=-csearch_path=us` 를 붙여, 같은 코드·같은 Alembic 마이그레이션이 그 스키마 안에서 돈다.

예전 미장 DB(SQLite 파일 `{DATA_DIR}/us_market.db`)가 남아 있고 `us` 스키마가 비어 있으면, 기동할 때 한 번 모든 테이블을
그대로 옮기고(id 유지, 시퀀스 맞춤) 파일 이름을 `us_market.db.migrated` 로 바꾼다 — 배포 순서가 어떻든 계좌가 새로 시작되지 않게.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Integer, create_engine, func, insert, select, text
from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

US_SCHEMA = "us"
CHUNK = 500


def is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def with_schema(url: str, schema: str) -> str:
    """Postgres 주소에 search_path 를 붙인다 (그 스키마에 테이블을 만들고 읽는다)."""
    u = make_url(url)
    q = dict(u.query)
    q["options"] = f"-csearch_path={schema}"
    return u.set(query=q).render_as_string(hide_password=False)


def schema_of(url: str) -> str | None:
    """주소의 search_path 스키마 (없으면 None)."""
    if not is_postgres(url):
        return None
    opt = make_url(url).query.get("options")
    opt = opt[0] if isinstance(opt, tuple) else opt
    if opt and "search_path=" in opt:
        return opt.split("search_path=", 1)[1].split()[0].split(",")[0]
    return None


def ensure_schema(url: str) -> None:
    """Postgres 주소에 스키마가 있으면 만든다 (마이그레이션 전에)."""
    schema = schema_of(url)
    if not schema:
        return
    if not schema.isidentifier():
        raise ValueError(f"스키마 이름이 이상해요: {schema!r}")
    eng = create_engine(url)
    try:
        with eng.begin() as con:
            con.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    finally:
        eng.dispose()


def copy_all(src_url: str, dst_url: str) -> dict[str, int]:
    """src 의 모든 앱 테이블을 dst 로 그대로 옮긴다 (dst 는 마이그레이션이 끝난 빈 DB). 테이블별 옮긴 행 수."""
    from app.db.models import Base

    src, dst = create_engine(src_url), create_engine(dst_url)
    out: dict[str, int] = {}
    try:
        with src.connect() as s, dst.begin() as d:
            for table in Base.metadata.sorted_tables:  # 외래키 순서
                if d.execute(select(func.count()).select_from(table)).scalar_one():
                    raise RuntimeError(f"옮길 곳에 이미 행이 있어요: {table.name} — 옮기지 않아요")
                rows = [dict(r._mapping) for r in s.execute(select(table))]
                for i in range(0, len(rows), CHUNK):
                    d.execute(insert(table), rows[i : i + CHUNK])
                out[table.name] = len(rows)
                pk = list(table.primary_key.columns)
                if is_postgres(dst_url) and rows and len(pk) == 1 and isinstance(pk[0].type, Integer):
                    col = pk[0].name
                    d.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col}'), "
                            f"(SELECT MAX({col}) FROM {table.name}))"
                        )
                    )
    finally:
        src.dispose()
        dst.dispose()
    return out


def _empty(url: str) -> bool:
    """옮겨 받을 DB 가 비어 있는지 — 모의계좌 상태·판단 기록·자산 기록이 하나도 없으면."""
    from app.db.models import AgentRun, EquitySnapshot, PaperAccountState

    eng = create_engine(url)
    try:
        with eng.connect() as con:
            return all(
                not con.execute(select(func.count()).select_from(t.__table__)).scalar_one()
                for t in (PaperAccountState, AgentRun, EquitySnapshot)
            )
    finally:
        eng.dispose()


def migrate_legacy_us(data_dir: str | Path, us_url: str) -> dict[str, int] | None:
    """예전 미장 SQLite 파일 → Postgres `us` 스키마 (한 번만). 옮겼으면 테이블별 행 수, 할 일이 없으면 None.

    us_url 은 마이그레이션이 끝난 뒤여야 한다. 옮긴 뒤 파일은 지우지 않고 이름만 바꿔 둔다(되돌리기용).
    """
    legacy = Path(data_dir) / "us_market.db"
    if not is_postgres(us_url) or not legacy.exists():
        return None
    if not _empty(us_url):
        log.warning(
            "미장 DB 옮기기 건너뜀: %s 스키마에 이미 기록이 있어요 (예전 파일은 그대로 둠)", schema_of(us_url)
        )
        return None
    counts = copy_all(f"sqlite:///{legacy}", us_url)
    for suffix in ("", "-wal", "-shm"):
        f = Path(f"{legacy}{suffix}")
        if f.exists():
            f.rename(f"{legacy}.migrated{suffix}")
    log.info("미장 DB 를 Postgres '%s' 스키마로 옮겼어요: %s", schema_of(us_url), counts)
    return counts
