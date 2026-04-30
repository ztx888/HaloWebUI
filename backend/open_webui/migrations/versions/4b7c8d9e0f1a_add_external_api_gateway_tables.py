"""Add external api gateway tables

Revision ID: 4b7c8d9e0f1a
Revises: 3d4e5f6a7b8c
Create Date: 2026-04-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "4b7c8d9e0f1a"
down_revision = "3d4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "external_api_client" not in tables:
        op.create_table(
            "external_api_client",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("owner_user_id", sa.Text(), nullable=False),
            sa.Column("api_key_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("key_prefix", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("allowed_protocols", sa.Text(), nullable=False),
            sa.Column("allowed_model_ids", sa.Text(), nullable=False),
            sa.Column("allow_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("rpm_limit", sa.Integer(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.Column("last_used_at", sa.BigInteger(), nullable=True),
        )

    if "external_api_audit_log" not in tables:
        op.create_table(
            "external_api_audit_log",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("client_id", sa.Text(), nullable=False),
            sa.Column("owner_user_id", sa.Text(), nullable=False),
            sa.Column("protocol", sa.Text(), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False),
            sa.Column("model", sa.Text(), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("tools_used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("ip_address", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("meta", sa.Text(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
        )

        op.create_index(
            "ix_external_api_audit_log_client_created",
            "external_api_audit_log",
            ["client_id", "created_at"],
            unique=False,
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "external_api_audit_log" in tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("external_api_audit_log")}
        if "ix_external_api_audit_log_client_created" in indexes:
            op.drop_index("ix_external_api_audit_log_client_created", table_name="external_api_audit_log")
        op.drop_table("external_api_audit_log")

    if "external_api_client" in tables:
        op.drop_table("external_api_client")
