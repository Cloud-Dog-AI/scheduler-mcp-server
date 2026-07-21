"""Live discovery via A2A agent-card polling.

For each enabled ``ProjectEntry`` the poller fetches the service's
`/.well-known/agent-card` and `/health`, then updates the entry's
``tools_list``, ``skills``, ``rbac_scopes`` and ``last_health_status`` fields.

The poller uses ``httpx`` synchronously for Phase 1 (one-shot use by the
``ProjectRegistryService.refresh()`` method); a background async loop lands
in Phase 2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from scheduler_mcp.registry.models import ProjectEntry


def poll_entry(
    entry: ProjectEntry, *, timeout_seconds: float = 5.0, client: httpx.Client | None = None
) -> ProjectEntry:
    """Update an entry in-place by hitting agent-card + health URLs.

    Failures are caught and recorded as ``last_health_status="down"`` — they
    never raise. Tests inject a ``client`` (httpx.MockTransport) so this is
    fully unit-testable without a network.
    """
    now = datetime.now(timezone.utc)
    own = client is None
    if own:
        client = httpx.Client(timeout=timeout_seconds)
    assert client is not None
    try:
        if entry.a2a_card_url:
            try:
                r = client.get(entry.a2a_card_url, timeout=timeout_seconds)
                if r.status_code == 200:
                    card: dict[str, Any] = r.json()
                    entry.skills = card.get("skills", {}) or {}
                    entry.tools_list = card.get("tools", {}) or {}
                    entry.rbac_scopes = list(card.get("rbac_scopes", []) or [])
                    entry.last_card_at = now
            except Exception:
                pass
        if entry.health_url:
            try:
                r = client.get(entry.health_url, timeout=timeout_seconds)
                entry.last_health_at = now
                if r.status_code == 200:
                    body: dict[str, Any] = {}
                    try:
                        body = r.json()
                    except Exception:
                        body = {}
                    entry.last_health_status = str(body.get("status", "ok")) or "ok"
                else:
                    entry.last_health_status = "degraded"
            except Exception:
                entry.last_health_at = now
                entry.last_health_status = "down"
    finally:
        if own and client is not None:
            client.close()
    return entry
