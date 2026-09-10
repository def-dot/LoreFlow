"""runs created_at/finished_at 改为 DateTime

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-21 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('runs') as batch:
        batch.alter_column('created_at', type_=sa.DateTime())
        batch.alter_column('finished_at', type_=sa.DateTime())


def downgrade() -> None:
    with op.batch_alter_table('runs') as batch:
        batch.alter_column('created_at', type_=sa.String())
        batch.alter_column('finished_at', type_=sa.String())
