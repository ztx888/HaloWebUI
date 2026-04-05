"""Add skill table for reusable AI skills

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-03-04 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "skill" not in inspector.get_table_names():
        op.create_table(
            "skill",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), server_default=""),
            sa.Column("content", sa.Text(), server_default=""),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("access_control", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
        )
        op.create_index("ix_skill_user_id", "skill", ["user_id"])

    # Enhance note table: add data, meta, access_control if missing
    note_columns = {c["name"] for c in inspector.get_columns("note")}

    if "data" not in note_columns:
        op.add_column("note", sa.Column("data", sa.JSON(), nullable=True))
    if "meta" not in note_columns:
        op.add_column("note", sa.Column("meta", sa.JSON(), nullable=True))
    if "access_control" not in note_columns:
        op.add_column("note", sa.Column("access_control", sa.JSON(), nullable=True))

    # Enhance folder table: add system_prompt if missing
    folder_columns = {c["name"] for c in inspector.get_columns("folder")}

    if "system_prompt" not in folder_columns:
        op.add_column(
            "folder", sa.Column("system_prompt", sa.Text(), nullable=True)
        )

    # Enhance prompt table: add is_active if missing
    prompt_columns = {c["name"] for c in inspector.get_columns("prompt")}

    if "is_active" not in prompt_columns:
        op.add_column(
            "prompt",
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Remove folder.system_prompt
    folder_columns = {c["name"] for c in inspector.get_columns("folder")}
    if "system_prompt" in folder_columns:
        op.drop_column("folder", "system_prompt")

    # Remove prompt.is_active
    prompt_columns = {c["name"] for c in inspector.get_columns("prompt")}
    if "is_active" in prompt_columns:
        op.drop_column("prompt", "is_active")

    # Remove note enhancements
    note_columns = {c["name"] for c in inspector.get_columns("note")}
    for col in ("access_control", "meta", "data"):
        if col in note_columns:
            op.drop_column("note", col)

    # Drop skill table
    if "skill" in inspector.get_table_names():
        op.drop_index("ix_skill_user_id", "skill")
        op.drop_table("skill")
