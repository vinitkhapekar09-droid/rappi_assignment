"""Retain historical supplier terms referenced by immutable recommendations.

Revision ID: 8b28a4b1c204
Revises: 153831c6769a
"""
from alembic import op
import sqlalchemy as sa

revision = "8b28a4b1c204"
down_revision = "153831c6769a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("supplier_offers", sa.Column(
        "is_current", sa.Boolean(), nullable=False, server_default=sa.true(),
    ))
    op.drop_constraint("uq_supplier_case_name", "supplier_offers", type_="unique")
    op.create_index("uq_supplier_case_name", "supplier_offers", ["case_id", "supplier_name"],
                    unique=True, postgresql_where=sa.text("is_current = true"))


def downgrade():
    # Refuses to downgrade if historical rows violate the old uniqueness rule;
    # history is never deleted to force a rollback through.
    op.drop_index("uq_supplier_case_name", table_name="supplier_offers")
    op.create_unique_constraint("uq_supplier_case_name", "supplier_offers", ["case_id", "supplier_name"])
    op.drop_column("supplier_offers", "is_current")
