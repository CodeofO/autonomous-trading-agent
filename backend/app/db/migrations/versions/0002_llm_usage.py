"""llm usage log (+ backfill from agent_runs.usage)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    usage = op.create_table('llm_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('cached_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('max_input_tokens', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_usage_ts'), ['ts'], unique=False)
        batch_op.create_index(batch_op.f('ix_llm_usage_run_id'), ['run_id'], unique=False)

    # 예전 판단 세션은 세션 합계만 있다 → 세션당 한 줄 (호출별 내역·캐시 추정 없음)
    runs = sa.table(
        'agent_runs',
        sa.column('id', sa.Integer), sa.column('model', sa.String), sa.column('mode', sa.String),
        sa.column('started_at', sa.DateTime(timezone=True)), sa.column('usage', sa.JSON),
    )
    rows = []
    for r in op.get_bind().execute(sa.select(runs.c.id, runs.c.model, runs.c.mode, runs.c.started_at, runs.c.usage)):
        u = r.usage or {}
        inp, out = int(u.get('prompt_tokens') or 0), int(u.get('completion_tokens') or 0)
        if r.mode == 'rule' or (inp == 0 and out == 0):
            continue
        rows.append({
            'ts': r.started_at, 'kind': 'session', 'run_id': r.id, 'model': r.model or '', 'calls': 0,
            'input_tokens': inp, 'cached_tokens': 0, 'output_tokens': out, 'max_input_tokens': 0,
            'source': 'backfill',
        })
    if rows:
        op.bulk_insert(usage, rows)


def downgrade() -> None:
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_usage_run_id'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_ts'))
    op.drop_table('llm_usage')
