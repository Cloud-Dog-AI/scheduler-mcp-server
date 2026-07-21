"""Static project registry seed from Terraform `*_containers.tf.json`.

W28K-1406. Resolves Gary's brief: every deployed service has a declarative
Terraform record. This loader parses the JSON, extracts service name,
hostname, image, exposed ports, and produces ``ProjectEntry`` records the
``ProjectRegistryService`` can persist.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from scheduler_mcp.registry.models import ProjectEntry


def discover_files(globs: list[str]) -> list[Path]:
    """Expand a list of glob patterns to existing file paths."""
    files: list[Path] = []
    for pattern in globs:
        for match in glob.iglob(pattern):
            p = Path(match)
            if p.is_file():
                files.append(p)
    return sorted(files)


def _extract_container_blocks(payload: dict) -> list[dict]:
    """Return docker_container blocks from a tf.json file."""
    blocks: list[dict] = []
    resource = payload.get("resource", {})
    container = resource.get("docker_container") or {}
    if isinstance(container, dict):
        for instance_name, instance_def in container.items():
            if isinstance(instance_def, list):
                for entry in instance_def:
                    if isinstance(entry, dict):
                        entry["__tf_name__"] = instance_name
                        blocks.append(entry)
            elif isinstance(instance_def, dict):
                instance_def["__tf_name__"] = instance_name
                blocks.append(instance_def)
    return blocks


def _entry_from_block(block: dict, *, source_file: str) -> ProjectEntry | None:
    """Build a ProjectEntry from a single docker_container block."""
    name = block.get("name") or block.get("__tf_name__")
    if not name:
        return None
    hostname = block.get("hostname") or f"{name}.cloud-dog.net"
    image_pin = block.get("image")
    base_url = f"https://{hostname}"
    entry = ProjectEntry(
        project_id=str(name),
        name=str(name),
        service_kind="mcp_tool",
        tenant_id="default",
        base_url=base_url,
        mcp_url=f"{base_url}/mcp",
        a2a_card_url=f"{base_url}/.well-known/agent-card",
        health_url=f"{base_url}/health",
        terraform_source=source_file,
        image_pin=image_pin if isinstance(image_pin, str) else None,
        enabled=True,
    )
    return entry


def load_entries(globs: list[str]) -> list[ProjectEntry]:
    """Parse all matching tf.json files and produce ProjectEntry records."""
    entries: list[ProjectEntry] = []
    for path in discover_files(globs):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for block in _extract_container_blocks(payload):
            entry = _entry_from_block(block, source_file=str(path))
            if entry is not None:
                entries.append(entry)
    return entries
