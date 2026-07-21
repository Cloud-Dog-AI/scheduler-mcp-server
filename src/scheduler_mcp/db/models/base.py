"""Declarative base for scheduler-mcp-server models.

Uses cloud_dog_db.models.base.PlatformBase + naming_convention so the
DDL is consistent across Cloud-Dog services and Alembic autogenerate
produces stable index/constraint names.
"""

from __future__ import annotations

from sqlalchemy.orm import declarative_base

# Try the platform Base first; if the package isn't import-compatible at test
# time we still need a Base whose metadata is non-empty for tests. The
# platform import IS the canonical path — there is no bespoke alternative.
try:
    from cloud_dog_db.models.base import PlatformBase as Base  # noqa: F401
    from cloud_dog_db.models.base import naming_convention
except Exception:  # pragma: no cover — only triggers in stripped-down test env
    from sqlalchemy import MetaData

    naming_convention = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    Base = declarative_base(metadata=MetaData(naming_convention=naming_convention))


metadata = Base.metadata
