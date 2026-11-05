"""llm_usage → exact per-call cost ledger (D-66)

호출마다 어느 제공사·모델(실제로 답한 모델)·계좌가 몇 토큰을 썼고 실제 요금이 얼마인지 남긴다.
- market·strategy: 어느 시장·계좌(주 계좌 '' / 실험실 전략 id)의 호출인지. 모든 기록은 국장 DB 한 곳(장부)에 모은다
- profile·provider: 무료/유료, openai | google | openrouter | local(자체 서버) | gateway(알 수 없는 유료 주소)
- served_model·upstream·response_id: 응답이 알려 준 실제 모델·OpenRouter 가 고른 공급사·응답 id (대조용)
- cache_read_tokens: 제공사가 알려 준 캐시 적중 입력 (알려 주지 않으면 NULL — cached_tokens 는 예전 추정)
- cache_write_tokens·reasoning_tokens: 캐시 쓰기·추론 토큰 (추론은 출력에 포함)
- cost_usd·cost_source: 요금과 출처 reported(제공사 보고) | priced(공식 단가 계산) | free | unpriced | estimated(예전 기록)
- origin: 미장·실험실 DB 에서 옮겨 온 기록의 원래 자리 (중복 없이 한 번만 옮기려고)
Postgres(국장)·SQLite(미장·실험실) 모두 같은 연산이다.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05 21:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS: list[sa.Column] = [
    sa.Column('market', sa.String(length=8), nullable=False, server_default=''),
    sa.Column('strategy', sa.String(length=24), nullable=False, server_default=''),
    sa.Column('profile', sa.String(length=8), nullable=False, server_default=''),
    sa.Column('provider', sa.String(length=16), nullable=False, server_default=''),
    sa.Column('served_model', sa.String(length=128), nullable=False, server_default=''),
    sa.Column('upstream', sa.String(length=64), nullable=False, server_default=''),
    sa.Column('response_id', sa.String(length=128), nullable=False, server_default=''),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=True),
    sa.Column('cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('reasoning_tokens', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('cost_source', sa.String(length=12), nullable=False, server_default=''),
    sa.Column('origin', sa.String(length=48), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        for col in COLUMNS:
            batch_op.add_column(col)
        batch_op.create_index('ix_llm_usage_origin', ['origin'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.drop_index('ix_llm_usage_origin')
        for col in reversed(COLUMNS):
            batch_op.drop_column(col.name)
