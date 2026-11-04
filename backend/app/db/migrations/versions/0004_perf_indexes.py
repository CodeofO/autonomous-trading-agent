"""indexes for filtered/ordered columns, drop redundant daily_prices symbol index (perf B8)

필터·정렬에 쓰는데 인덱스가 없던 열 (docs/perf/baseline-backend.md §3):
- agent_runs.started_at (기준 시각 필터·일일 리포트), (status, id) (기억: 완료 세션 최근 3개)
- orders.created_at (기준 시각 필터·일일 리포트), run_id (기록 정리·외래키), broker_order_id (체결 반영)
- fills.filled_at (기준 시각 필터·매매 원장·일일 리포트), symbol (차트 체결 표시)
- agent_decisions.created_at (기준 시각 필터), order_id (기록 정리·외래키)
- chat_messages (role, id) (기억: 내 메시지 최근 5개 — 예전에는 기본키를 거꾸로 훑었다)
- memory_cards (symbol, kind) (같은 카드 중복 확인)
- api_cache.fetched_at (하루 한 번 만료 정리)
ix_daily_prices_symbol 은 uq_daily_price(symbol, day) 의 앞 열과 같아 지운다.
Postgres(국장)·SQLite(미장) 모두 같은 연산이다.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 00:30:00
"""
from collections.abc import Sequence

from alembic import op


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: list[tuple[str, str, list[str]]] = [
    ('ix_agent_runs_started_at', 'agent_runs', ['started_at']),
    ('ix_agent_runs_status_id', 'agent_runs', ['status', 'id']),
    ('ix_orders_created_at', 'orders', ['created_at']),
    ('ix_orders_run_id', 'orders', ['run_id']),
    ('ix_orders_broker_order_id', 'orders', ['broker_order_id']),
    ('ix_fills_filled_at', 'fills', ['filled_at']),
    ('ix_fills_symbol', 'fills', ['symbol']),
    ('ix_agent_decisions_created_at', 'agent_decisions', ['created_at']),
    ('ix_agent_decisions_order_id', 'agent_decisions', ['order_id']),
    ('ix_chat_messages_role_id', 'chat_messages', ['role', 'id']),
    ('ix_memory_cards_symbol_kind', 'memory_cards', ['symbol', 'kind']),
    ('ix_api_cache_fetched_at', 'api_cache', ['fetched_at']),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols, unique=False)
    op.drop_index('ix_daily_prices_symbol', table_name='daily_prices')


def downgrade() -> None:
    op.create_index('ix_daily_prices_symbol', 'daily_prices', ['symbol'], unique=False)
    for name, table, _cols in reversed(INDEXES):
        op.drop_index(name, table_name=table)
