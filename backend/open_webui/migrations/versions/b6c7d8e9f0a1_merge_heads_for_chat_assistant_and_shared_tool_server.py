"""Merge parallel heads for chat assistant and shared tool server

Revision ID: b6c7d8e9f0a1
Revises: 6b1c2d3e4f5a, a2b3c4d5e6f7
Create Date: 2026-04-16 16:40:00.000000

This migration intentionally does not modify schema objects.
It only merges two parallel Alembic heads into a single linear head so
startup migrations can proceed normally.
"""

revision = "b6c7d8e9f0a1"
down_revision = ("6b1c2d3e4f5a", "a2b3c4d5e6f7")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
