"""hardening_isbn10_index

Revision ID: b7e2a1c904f3
Revises: a5bfb4c64f04
Create Date: 2026-06-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b7e2a1c904f3"
down_revision: Union[str, None] = "a5bfb4c64f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_books_isbn10"), ["isbn10"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_books_isbn10"))
