"""SQL-backed IDAM admin routers — W28K-1407 F-1407-8.

Host-application SQL wiring for the IDAM admin surface (the pattern the
``cloud_dog_idam`` router docstring endorses). Persists users/groups/roles/
api-keys/memberships/bindings to SQL via the platform
``cloud_dog_idam.storage.sqlalchemy`` ORM models over the scheduler's
``SyncSessionManager`` session (one session per request). API-key secrets use
the platform ``api_keys.hashing`` primitive; resource-registry reuses the
platform ``ResourceRegistryService``.

Route contracts (paths + payload + response shapes) match the previously
mounted in-memory ``cloud_dog_idam.api.fastapi`` routers so the WebUI + the
baseline IT remain stable; the difference is durability (survives restart).

Unlike the platform in-memory routers (which had no auth dependency), these
routers enforce the PS-70 IDAM scopes already catalogued in
``idam.default_admin_scopes`` — anonymous callers are denied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cloud_dog_idam.rbac.resource_registry import ResourceRegistryService
from cloud_dog_idam.storage.sqlalchemy import models as m
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.idam_sql import _scopes_from_json, _scopes_to_json, mint_api_key, new_id

user_router = APIRouter(tags=["idam-users"])
group_router = APIRouter(tags=["idam-groups"])
role_router = APIRouter(tags=["idam-roles"])
api_key_router = APIRouter(tags=["idam-api-keys"])
idam_v1_router = APIRouter(tags=["idam-v1"])

_resource_registry = ResourceRegistryService()


def _require(request: Request, scope: str):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _iso(v) -> str | None:
    return v.isoformat() if v else None


# ---------------------------------------------------------------- users
def _user_dto(u: m.UserORM) -> dict[str, Any]:
    return {
        # W28K-1408 — `id` alias: the shared @cloud-dog/idam UI keys users by
        # `id` (e.g. the api-key owner <option value={user.id}>); without it the
        # owner Select submits the username and create 400s.
        "id": u.user_id,
        "user_id": u.user_id,
        "username": u.username,
        "email": u.email,
        "display_name": u.display_name,
        "status": u.status,
        "role": u.role,
        "tenant_id": u.tenant_id,
        "created_at": _iso(u.created_at),
        "updated_at": _iso(u.updated_at),
    }


@user_router.post("/users", status_code=201)
def create_user(payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "users.write")
    now = _now()
    with get_session_manager().session() as session:
        u = m.UserORM(
            user_id=new_id("usr"),
            username=str(payload.get("username", "")),
            email=str(payload.get("email", "")),
            display_name=str(payload.get("display_name", "")),
            role=str(payload.get("role", "user")),
            status="active",
            tenant_id=payload.get("tenant_id"),
            created_at=now,
            updated_at=now,
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        return _user_dto(u)


@user_router.get("/users")
def list_users(request: Request) -> list[dict[str, Any]]:
    _require(request, "users.read")
    with get_session_manager().session() as session:
        rows = session.execute(select(m.UserORM)).scalars().all()
        return [_user_dto(u) for u in rows]


@user_router.get("/users/{user_id}")
def get_user(user_id: str, request: Request) -> dict[str, Any]:
    _require(request, "users.read")
    with get_session_manager().session() as session:
        u = session.get(m.UserORM, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _user_dto(u)


@user_router.patch("/users/{user_id}")
def update_user(user_id: str, payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "users.write")
    with get_session_manager().session() as session:
        u = session.get(m.UserORM, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Not found")
        for key in ("username", "email", "display_name", "role", "status"):
            if key in payload and payload[key] is not None:
                setattr(u, key, payload[key])
        u.updated_at = _now()
        session.commit()
        session.refresh(u)
        return _user_dto(u)


@user_router.delete("/users/{user_id}")
def delete_user(user_id: str, request: Request) -> dict[str, bool]:
    """Soft-delete: status -> disabled (matches the cloud_dog_idam contract;
    GET after delete returns the user with status='disabled')."""
    _require(request, "users.write")
    with get_session_manager().session() as session:
        u = session.get(m.UserORM, user_id)
        if u is None:
            return {"ok": False}
        u.status = "disabled"
        u.updated_at = _now()
        session.commit()
        return {"ok": True}


@user_router.get("/users/{user_id}/identities")
def user_identities(user_id: str, request: Request) -> list[dict[str, Any]]:  # noqa: ARG001
    _require(request, "users.read")
    return []


@user_router.get("/users/{user_id}/roles")
def user_roles(user_id: str, request: Request) -> list[str]:
    _require(request, "users.read")
    with get_session_manager().session() as session:
        role_ids = (
            session.execute(select(m.UserRoleORM.role_id).where(m.UserRoleORM.user_id == user_id)).scalars().all()
        )
        if not role_ids:
            return []
        names = session.execute(select(m.RoleORM.name).where(m.RoleORM.role_id.in_(list(role_ids)))).scalars().all()
        return sorted(names)


@user_router.get("/users/{user_id}/groups")
def user_groups(user_id: str, request: Request) -> list[str]:
    _require(request, "users.read")
    with get_session_manager().session() as session:
        group_ids = (
            session.execute(select(m.GroupMembershipORM.group_id).where(m.GroupMembershipORM.user_id == user_id))
            .scalars()
            .all()
        )
        return list(group_ids)


# ---------------------------------------------------------------- groups
def _group_dto(g: m.GroupORM) -> dict[str, Any]:
    return {
        "group_id": g.group_id,
        "name": g.name,
        "description": g.description,
        "tenant_id": g.tenant_id,
        "created_at": _iso(g.created_at),
        "updated_at": _iso(g.updated_at),
    }


@group_router.post("/groups", status_code=201)
def create_group(payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "groups.write")
    now = _now()
    with get_session_manager().session() as session:
        g = m.GroupORM(
            group_id=new_id("grp"),
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            tenant_id=payload.get("tenant_id"),
            created_at=now,
            updated_at=now,
        )
        session.add(g)
        session.commit()
        session.refresh(g)
        return _group_dto(g)


@group_router.get("/groups")
def list_groups(request: Request) -> list[dict[str, Any]]:
    _require(request, "groups.read")
    with get_session_manager().session() as session:
        rows = session.execute(select(m.GroupORM)).scalars().all()
        return [_group_dto(g) for g in rows]


@group_router.get("/groups/{group_id}")
def get_group(group_id: str, request: Request) -> dict[str, Any]:
    _require(request, "groups.read")
    with get_session_manager().session() as session:
        g = session.get(m.GroupORM, group_id)
        if g is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _group_dto(g)


@group_router.patch("/groups/{group_id}")
def update_group(group_id: str, payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "groups.write")
    with get_session_manager().session() as session:
        g = session.get(m.GroupORM, group_id)
        if g is None:
            raise HTTPException(status_code=404, detail="Not found")
        if "name" in payload and payload["name"] is not None:
            g.name = str(payload["name"])
        if "description" in payload and payload["description"] is not None:
            g.description = str(payload["description"])
        g.updated_at = _now()
        session.commit()
        session.refresh(g)
        return _group_dto(g)


@group_router.delete("/groups/{group_id}")
def delete_group(group_id: str, request: Request) -> dict[str, bool]:
    _require(request, "groups.write")
    with get_session_manager().session() as session:
        g = session.get(m.GroupORM, group_id)
        if g is None:
            return {"ok": False}
        # remove memberships first (no cascade defined on the membership FK)
        for mem in (
            session.execute(select(m.GroupMembershipORM).where(m.GroupMembershipORM.group_id == group_id))
            .scalars()
            .all()
        ):
            session.delete(mem)
        session.delete(g)
        session.commit()
        return {"ok": True}


@group_router.post("/groups/{group_id}/members", status_code=201)
def add_group_member(group_id: str, payload: dict, request: Request) -> dict[str, bool]:
    _require(request, "groups.write")
    user_id = str(payload.get("user_id", ""))
    now = _now()
    with get_session_manager().session() as session:
        existing = session.get(m.GroupMembershipORM, {"user_id": user_id, "group_id": group_id})
        if existing is None:
            session.add(
                m.GroupMembershipORM(
                    user_id=user_id,
                    group_id=group_id,
                    role_in_group=str(payload.get("role_in_group", "member")),
                    created_at=now,
                )
            )
            session.commit()
        return {"ok": True}


@group_router.delete("/groups/{group_id}/members/{user_id}")
def remove_group_member(group_id: str, user_id: str, request: Request) -> dict[str, bool]:
    _require(request, "groups.write")
    with get_session_manager().session() as session:
        mem = session.get(m.GroupMembershipORM, {"user_id": user_id, "group_id": group_id})
        if mem is not None:
            session.delete(mem)
            session.commit()
        return {"ok": True}


# ---------------------------------------------------------------- roles
def _role_dto(r: m.RoleORM) -> dict[str, Any]:
    return {"role_id": r.role_id, "name": r.name, "description": r.description, "created_at": _iso(r.created_at)}


@role_router.post("/roles", status_code=201)
def create_role(payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "roles.write")
    now = _now()
    with get_session_manager().session() as session:
        r = m.RoleORM(
            role_id=new_id("rol"),
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            created_at=now,
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        return _role_dto(r)


@role_router.get("/roles")
def list_roles(request: Request) -> list[dict[str, Any]]:
    _require(request, "roles.read")
    with get_session_manager().session() as session:
        rows = session.execute(select(m.RoleORM)).scalars().all()
        return [_role_dto(r) for r in rows]


@role_router.get("/roles/{role_id}")
def get_role(role_id: str, request: Request) -> dict[str, Any]:
    _require(request, "roles.read")
    with get_session_manager().session() as session:
        r = session.get(m.RoleORM, role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _role_dto(r)


@role_router.patch("/roles/{role_id}")
def update_role(role_id: str, payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "roles.write")
    with get_session_manager().session() as session:
        r = session.get(m.RoleORM, role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Not found")
        if "name" in payload and payload["name"] is not None:
            r.name = str(payload["name"])
        if "description" in payload and payload["description"] is not None:
            r.description = str(payload["description"])
        session.commit()
        session.refresh(r)
        return _role_dto(r)


@role_router.delete("/roles/{role_id}")
def delete_role(role_id: str, request: Request) -> dict[str, bool]:
    _require(request, "roles.write")
    with get_session_manager().session() as session:
        r = session.get(m.RoleORM, role_id)
        if r is None:
            return {"ok": False}
        session.delete(r)
        session.commit()
        return {"ok": True}


# ---------------------------------------------------------------- api-keys
def _api_key_dto(k: m.APIKeyORM) -> dict[str, Any]:
    scopes = _scopes_from_json(k.scopes)
    return {
        # W28K-1408 — aliases for the shared @cloud-dog/idam ApiKeyRecord shape
        # (id, user_id, disabled, groups) so the API Keys page renders the owner
        # column + row identity correctly. Canonical fields are kept alongside.
        "id": k.api_key_id,
        "user_id": k.owner_user_id,
        "disabled": k.status != "active",
        "groups": scopes,
        "api_key_id": k.api_key_id,
        "owner_user_id": k.owner_user_id,
        "key_prefix": k.key_prefix,
        "status": k.status,
        "scopes": scopes,
        "expires_at": _iso(k.expires_at),
        "created_at": _iso(k.created_at),
    }


@api_key_router.post("/api-keys", status_code=201)
def create_api_key(payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "apikeys.write")
    # W28K-1408 — accept the shared @cloud-dog/idam UI payload shape (user_id +
    # groups) as well as the canonical owner_user_id + scopes. The shared
    # IdamApiKeysPage posts {user_id, name, groups}; without these aliases owner
    # defaulted to a non-existent "system" user -> api_keys FK 500. Owner is now
    # validated against the users table (400, not 500, on an unknown owner).
    owner = str(payload.get("owner_user_id") or payload.get("user_id") or "").strip()
    scopes = payload.get("scopes") or payload.get("groups") or []
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    if not owner:
        raise HTTPException(status_code=400, detail="owner_user_id (or user_id) is required")
    raw, key_hash = mint_api_key()
    now = _now()
    with get_session_manager().session() as session:
        if session.get(m.UserORM, owner) is None:
            raise HTTPException(status_code=400, detail=f"owner user not found: {owner}")
        k = m.APIKeyORM(
            api_key_id=new_id("key"),
            owner_user_id=owner,
            key_hash=key_hash,
            key_prefix="cd_",
            status="active",
            scopes=_scopes_to_json(list(scopes)),
            created_at=now,
        )
        session.add(k)
        session.commit()
        # W28K-1408 — `api_key.key` envelope alias: the shared @cloud-dog/idam UI
        # reads the reveal-once secret from `payload.api_key.key`. Canonical
        # raw_key / api_key_id are kept for direct API callers.
        return {
            "raw_key": raw,
            "api_key_id": k.api_key_id,
            "api_key": {**_api_key_dto(k), "key": raw},
        }


@api_key_router.get("/api-keys")
def list_api_keys(owner_user_id: str, request: Request) -> list[dict[str, Any]]:
    _require(request, "apikeys.read")
    with get_session_manager().session() as session:
        rows = session.execute(select(m.APIKeyORM).where(m.APIKeyORM.owner_user_id == owner_user_id)).scalars().all()
        return [_api_key_dto(k) for k in rows]


@api_key_router.post("/api-keys/{key_id}/rotate")
def rotate_api_key(key_id: str, request: Request) -> dict[str, Any]:
    _require(request, "apikeys.write")
    raw, key_hash = mint_api_key()
    now = _now()
    with get_session_manager().session() as session:
        cur = session.get(m.APIKeyORM, key_id)
        if cur is None:
            raise HTTPException(status_code=404, detail="Not found")
        cur.status = "rotating"
        new_key = m.APIKeyORM(
            api_key_id=new_id("key"),
            owner_user_id=cur.owner_user_id,
            key_hash=key_hash,
            key_prefix=cur.key_prefix,
            status="active",
            scopes=cur.scopes,
            created_at=now,
        )
        session.add(new_key)
        session.commit()
        return {"raw_key": raw, "api_key_id": new_key.api_key_id}


@api_key_router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, request: Request) -> dict[str, bool]:
    _require(request, "apikeys.write")
    with get_session_manager().session() as session:
        k = session.get(m.APIKeyORM, key_id)
        if k is None:
            return {"ok": False}
        k.status = "revoked"
        k.revoked_at = _now()
        session.commit()
        return {"ok": True}


# ---------------------------------------------------------------- idam/v1
def _binding_dto(b: m.RBACBindingORM) -> dict[str, Any]:
    return {
        "binding_id": b.binding_id,
        "subject_type": b.subject_type,
        "subject_id": b.subject_id,
        "project": b.project,
        "resource_type": b.resource_type,
        "resource_id": b.resource_id,
        "permission": b.permission,
        "granted_by": b.granted_by,
        "created_at": _iso(b.created_at),
    }


@idam_v1_router.get("/idam/v1/resource-registry")
def resource_registry(request: Request, project: str | None = None) -> dict[str, Any]:
    _require(request, "rbac.read")
    return _resource_registry.to_response(project=project)


@idam_v1_router.get("/idam/v1/rbac/bindings")
def list_rbac_bindings(
    request: Request,
    subject_type: str | None = None,
    subject_id: str | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    _require(request, "rbac.read")
    with get_session_manager().session() as session:
        q = select(m.RBACBindingORM)
        if subject_type:
            q = q.where(m.RBACBindingORM.subject_type == subject_type)
        if subject_id:
            q = q.where(m.RBACBindingORM.subject_id == subject_id)
        if project:
            q = q.where(m.RBACBindingORM.project == project)
        rows = session.execute(q).scalars().all()
        return [_binding_dto(b) for b in rows]


@idam_v1_router.post("/idam/v1/rbac/bindings", status_code=201)
def create_rbac_binding(payload: dict, request: Request) -> dict[str, Any]:
    _require(request, "rbac.write")
    now = _now()
    with get_session_manager().session() as session:
        b = m.RBACBindingORM(
            binding_id=new_id("bnd"),
            subject_type=str(payload.get("subject_type", "user")),
            subject_id=str(payload.get("subject_id", "")),
            project=str(payload.get("project", "platform")),
            resource_type=str(payload.get("resource_type", "system")),
            resource_id=str(payload.get("resource_id", "*")),
            permission=str(payload.get("permission", payload.get("role_id", "read"))),
            granted_by=str(payload.get("granted_by", "system")),
            created_at=now,
        )
        session.add(b)
        session.commit()
        session.refresh(b)
        return _binding_dto(b)


@idam_v1_router.get("/idam/v1/rbac/bindings/{binding_id}")
def get_rbac_binding(binding_id: str, request: Request) -> dict[str, Any]:
    _require(request, "rbac.read")
    with get_session_manager().session() as session:
        b = session.get(m.RBACBindingORM, binding_id)
        if b is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _binding_dto(b)


@idam_v1_router.delete("/idam/v1/rbac/bindings/{binding_id}")
def delete_rbac_binding(binding_id: str, request: Request) -> dict[str, bool]:
    _require(request, "rbac.write")
    with get_session_manager().session() as session:
        b = session.get(m.RBACBindingORM, binding_id)
        if b is None:
            return {"ok": False}
        session.delete(b)
        session.commit()
        return {"ok": True}
