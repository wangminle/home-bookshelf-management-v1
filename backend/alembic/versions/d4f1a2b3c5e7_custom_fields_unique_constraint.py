"""custom_fields_unique_constraint

Revision ID: d4f1a2b3c5e7
Revises: c8d9e0f1a2b3
Create Date: 2026-07-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f1a2b3c5e7"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 历史脏数据：同一 (entity_type, entity_id, field_key) 可能有多行。
    # 建唯一约束前先保留 id 最大（通常最新）的一行，删除其余重复。
    op.execute(
        sa.text(
            """
            DELETE FROM custom_fields
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM custom_fields
                GROUP BY entity_type, entity_id, field_key
            )
            """
        )
    )
    with op.batch_alter_table("custom_fields", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_custom_fields_entity_key", ["entity_type", "entity_id", "field_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("custom_fields", schema=None) as batch_op:
        batch_op.drop_constraint("uq_custom_fields_entity_key", type_="unique")
