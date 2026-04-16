"""Add shared tool server table

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-16 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shared_tool_server",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("connection_payload", sa.Text(), nullable=False),
        sa.Column("display_metadata", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.sql.expression.true(),
        ),
        sa.Column("access_control", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_shared_tool_server_owner_kind",
        "shared_tool_server",
        ["owner_user_id", "kind"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_shared_tool_server_owner_kind", table_name="shared_tool_server")
    op.drop_table("shared_tool_server")
