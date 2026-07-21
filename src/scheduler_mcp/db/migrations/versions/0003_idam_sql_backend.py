"""SQL-backed IDAM tables — W28K-1407 F-1407-8 (closes the W28K-1404c carve-out).

Revision ID: 0003_idam_sql_backend
Revises: 0002_approvals
Create Date: 2026-06-15

Creates the cloud_dog_idam SQLAlchemy tables (by their ORM __tablename__) so the
scheduler's SQL-backed IDAM admin routers persist users/groups/roles/api-keys/
bindings + memberships across restart. Column sets mirror
``cloud_dog_idam.storage.sqlalchemy.models``. All DDL via op.create_table — no
SQLite ``executescript()`` (AGENT-LESSONS §6.43); upgrade->downgrade->upgrade
replays cleanly.

The brief's indicative filename was "0002_idam_sql_backend.py"; it is realised
as 0003 because F-1407-3's approvals migration takes 0002.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_idam_sql_backend"
down_revision: str | None = "0002_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="user"),
        sa.Column("is_system_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "groups",
        sa.Column("group_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "roles",
        sa.Column("role_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "group_memberships",
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), primary_key=True),
        sa.Column("group_id", sa.String(length=64), sa.ForeignKey("groups.group_id"), primary_key=True),
        sa.Column("role_in_group", sa.String(length=64), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), primary_key=True),
        sa.Column("role_id", sa.String(length=64), sa.ForeignKey("roles.role_id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "rbac_bindings",
        sa.Column("binding_id", sa.String(length=64), primary_key=True),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("project", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False, server_default="*"),
        sa.Column("permission", sa.String(length=128), nullable=False),
        sa.Column("granted_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rbac_bindings_subject_id", "rbac_bindings", ["subject_id"])
    op.create_index("ix_rbac_bindings_project", "rbac_bindings", ["project"])

    op.create_table(
        "api_keys",
        sa.Column("api_key_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False, server_default="cd_"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_owner_user_id", "api_keys", ["owner_user_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_rbac_bindings_project", table_name="rbac_bindings")
    op.drop_index("ix_rbac_bindings_subject_id", table_name="rbac_bindings")
    op.drop_table("rbac_bindings")
    op.drop_table("user_roles")
    op.drop_table("group_memberships")
    op.drop_table("roles")
    op.drop_table("groups")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
