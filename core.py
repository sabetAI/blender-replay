# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python helpers shared by Blender Replay and its unit tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
RECORDER_VERSION = "0.1.0"


def new_session(
    name: str,
    blender_version: str,
    scene_name: str,
    max_checkpoint_vertices: int,
) -> dict[str, Any]:
    """Return an empty, JSON-safe recording document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "recorder_version": RECORDER_VERSION,
        "name": name.strip() or "Recording",
        "started_at": datetime.now(UTC).isoformat(),
        "blender_version": blender_version,
        "source_scene": scene_name,
        "settings": {
            "max_checkpoint_vertices": max_checkpoint_vertices,
        },
        "events": [],
        "warnings": [],
    }


def validate_session(value: Any) -> dict[str, Any]:
    """Validate the stable parts of the on-disk recording contract."""
    if not isinstance(value, dict):
        raise ValueError("The recording root must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema version {value.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )
    if not isinstance(value.get("events"), list):
        raise ValueError("The recording must contain an events list")
    if not isinstance(value.get("warnings", []), list):
        raise ValueError("The recording warnings must be a list")
    return value


def operator_identifier_to_path(identifier: str) -> tuple[str, str]:
    """Convert Blender's RNA operator identifier into a bpy.ops path."""
    if "_OT_" in identifier:
        namespace, operator = identifier.split("_OT_", 1)
        return namespace.lower(), operator
    if "." in identifier:
        namespace, operator = identifier.split(".", 1)
        return namespace.lower(), operator
    raise ValueError(f"Unrecognised Blender operator identifier: {identifier!r}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def unseen_items(
    items: Iterable[tuple[int, str]],
    seen: dict[int, str],
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split pointer/fingerprint pairs into new and mutated operator records."""
    new: list[tuple[int, str]] = []
    mutated: list[tuple[int, str]] = []
    for pointer, fingerprint in items:
        if pointer not in seen:
            new.append((pointer, fingerprint))
        elif seen[pointer] != fingerprint:
            mutated.append((pointer, fingerprint))
    return new, mutated
