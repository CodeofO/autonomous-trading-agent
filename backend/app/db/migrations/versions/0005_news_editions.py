"""news room editions (D-56)

뉴스 룸(매일 07·12·17시 한국 시각 + 수동 새로고침)이 모은 섹션별 뉴스와 요약 한 판씩.
Postgres(국장)·SQLite(미장) 모두 같은 연산이다(미장 DB 에는 행이 쌓이지 않는다).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('news_editions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_news_editions_created_at', 'news_editions', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_news_editions_created_at', table_name='news_editions')
    op.drop_table('news_editions')
