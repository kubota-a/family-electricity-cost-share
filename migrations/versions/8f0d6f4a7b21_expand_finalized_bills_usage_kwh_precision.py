"""expand finalized_bills usage_kwh precision

Revision ID: 8f0d6f4a7b21
Revises: c954ac3c2182
Create Date: 2026-03-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f0d6f4a7b21'
down_revision = 'c954ac3c2182'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('finalized_bills', schema=None) as batch_op:
        batch_op.alter_column(
            'usage_kwh',
            existing_type=sa.NUMERIC(precision=6, scale=3),
            type_=sa.NUMERIC(precision=8, scale=3),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('finalized_bills', schema=None) as batch_op:
        batch_op.alter_column(
            'usage_kwh',
            existing_type=sa.NUMERIC(precision=8, scale=3),
            type_=sa.NUMERIC(precision=6, scale=3),
            existing_nullable=False,
        )
