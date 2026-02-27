"""add credit tables

Revision ID: a1b2c3d4e5f6
Revises: c440947495f3
Create Date: 2025-02-10 17:50:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Inspector

from open_webui.migrations.util import get_existing_tables

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "c440947495f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_existing_index_names():
    """Get all existing index names across all tables."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    existing_indexes = set()
    for table_name in inspector.get_table_names():
        for idx in inspector.get_indexes(table_name):
            existing_indexes.add(idx["name"])
    return existing_indexes


def _create_index_if_not_exists(index_name, table_name, columns, existing_indexes):
    """Create an index only if it doesn't already exist."""
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    # Create credit table
    if "credit" not in existing_tables:
        op.create_table(
            "credit",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.String(), nullable=False, unique=True),
            sa.Column("credit", sa.Numeric(precision=24, scale=12), nullable=True),
            sa.Column("updated_at", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=True),
        )

    # Create credit_log table
    if "credit_log" not in existing_tables:
        op.create_table(
            "credit_log",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("credit", sa.Numeric(precision=24, scale=12), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=True),
            # indexes
            sa.Index("ix_credit_log_user_id", "user_id"),
            sa.Index("ix_credit_log_created_at", "created_at"),
        )

    # Create trade_ticket table
    if "trade_ticket" not in existing_tables:
        op.create_table(
            "trade_ticket",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("amount", sa.Numeric(precision=24, scale=12), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=True),
            # indexes
            sa.Index("ix_trade_ticket_user_id", "user_id"),
            sa.Index("ix_trade_ticket_created_at", "created_at"),
        )

    # Create redemption_code table
    if "redemption_code" not in existing_tables:
        op.create_table(
            "redemption_code",
            sa.Column("code", sa.String(), primary_key=True, nullable=False),
            sa.Column("purpose", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("amount", sa.Numeric(precision=24, scale=12), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=True),
            sa.Column("expired_at", sa.BigInteger(), nullable=True),
            sa.Column("received_at", sa.BigInteger(), nullable=True),
            # indexes
            sa.Index("ix_redemption_code_purpose", "purpose"),
            sa.Index("ix_redemption_code_user_id", "user_id"),
            sa.Index("ix_redemption_code_created_at", "created_at"),
            sa.Index("ix_redemption_code_expired_at", "expired_at"),
            sa.Index("ix_redemption_code_received_at", "received_at"),
        )

    # ---------------------------------------------------------------
    # For existing tables that were created outside of migrations
    # (e.g. by SQLAlchemy create_all), ensure all indexes exist.
    # This handles the upgrade scenario where tables already exist
    # but may be missing indexes.
    # ---------------------------------------------------------------
    existing_indexes = _get_existing_index_names()

    # credit_log indexes
    _create_index_if_not_exists(
        "ix_credit_log_user_id", "credit_log", ["user_id"], existing_indexes
    )
    _create_index_if_not_exists(
        "ix_credit_log_created_at", "credit_log", ["created_at"], existing_indexes
    )

    # trade_ticket indexes
    _create_index_if_not_exists(
        "ix_trade_ticket_user_id", "trade_ticket", ["user_id"], existing_indexes
    )
    _create_index_if_not_exists(
        "ix_trade_ticket_created_at", "trade_ticket", ["created_at"], existing_indexes
    )

    # redemption_code indexes
    _create_index_if_not_exists(
        "ix_redemption_code_purpose", "redemption_code", ["purpose"], existing_indexes
    )
    _create_index_if_not_exists(
        "ix_redemption_code_user_id", "redemption_code", ["user_id"], existing_indexes
    )
    _create_index_if_not_exists(
        "ix_redemption_code_created_at",
        "redemption_code",
        ["created_at"],
        existing_indexes,
    )
    _create_index_if_not_exists(
        "ix_redemption_code_expired_at",
        "redemption_code",
        ["expired_at"],
        existing_indexes,
    )
    _create_index_if_not_exists(
        "ix_redemption_code_received_at",
        "redemption_code",
        ["received_at"],
        existing_indexes,
    )


def downgrade() -> None:
    op.drop_table("redemption_code")
    op.drop_table("trade_ticket")
    op.drop_table("credit_log")
    op.drop_table("credit")
