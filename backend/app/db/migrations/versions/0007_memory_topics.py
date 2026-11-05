"""memory topics — 대화에서 자라는 기억 (D-67)

투자자가 트레이딩 룸에서 꺼낸 주제(종목·업종·테마·운용 지시)마다 언급한 날·횟수·최근 인용과
매니저(LLM)의 판단(올림/유지/내림/잊기)을 남긴다. 중요도는 읽을 때 날짜로 다시 계산한다(app/agent/topics.py).
시장 스택마다 따로(각 DB). Postgres(국장)·SQLite(미장·실험실) 모두 같은 연산이다.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05 22:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('memory_topics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=True),
    sa.Column('days', sa.JSON(), nullable=False),
    sa.Column('mentions', sa.Integer(), nullable=False),
    sa.Column('quotes', sa.JSON(), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('llm_level', sa.Integer(), nullable=False),
    sa.Column('llm_reason', sa.Text(), nullable=False),
    sa.Column('llm_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('forgot_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pin', sa.String(length=8), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key', name='uq_memory_topics_key'),
    )


def downgrade() -> None:
    op.drop_table('memory_topics')
