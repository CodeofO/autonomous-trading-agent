"""benchmark_daily — daily closes of the indicator strip for day-level analysis (D-72)

코스피·코스닥·국고채 10년·QQQ·SPY·DIA 는 토스 일봉에서, 원/달러는 매일 16:10(한국 시각) 현재 환율로 남긴다.
국장(public)·미장(us) 스키마 모두 같은 연산이다(행은 국장 쪽에만 쌓인다).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06 07:40:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'benchmark_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=24), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', 'day', name='uq_benchmark_daily'),
    )
    op.create_index('ix_benchmark_daily_day', 'benchmark_daily', ['day'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_benchmark_daily_day', table_name='benchmark_daily')
    op.drop_table('benchmark_daily')
