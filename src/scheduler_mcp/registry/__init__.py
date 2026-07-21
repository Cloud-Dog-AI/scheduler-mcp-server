"""Project-registry — Gary's brief: scheduler must know preprod services.

Two-pronged design (per W28K-1403-design §3.7):
1. ``terraform_loader`` — static seed from `cloud-dog-repo/terraform/.../27 MLAgents/*_containers.tf.json`
2. ``agent_card_poller`` — live discovery from each service's `/.well-known/agent-card`

Both produce ``ProjectEntry`` records that the registry service persists via
``cloud_dog_db`` (no bespoke dict store; RULES §1.4) and caches via
``cloud_dog_cache``.
"""

from __future__ import annotations

from scheduler_mcp.registry.models import ProjectEntry
from scheduler_mcp.registry.service import ProjectRegistryService

__all__ = ["ProjectEntry", "ProjectRegistryService"]
