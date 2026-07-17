# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe conversion between Blender RNA values and JSON values."""

from __future__ import annotations

from typing import Any

import bpy

from .core import stable_digest


class _SkipValue:
    pass


SKIP = _SkipValue()


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, set):
        items = [to_jsonable(item) for item in sorted(value)]
        if any(item is SKIP for item in items):
            return SKIP
        return {"__type__": "set", "items": items}
    if isinstance(value, (tuple, list)) or value.__class__.__name__ == "bpy_prop_array":
        items = [to_jsonable(item) for item in value]
        if any(item is SKIP for item in items):
            return SKIP
        return items
    if isinstance(value, bpy.types.ID):
        return {
            "__type__": "id",
            "rna_type": value.bl_rna.identifier,
            "name": value.name,
        }
    if hasattr(value, "bl_rna"):
        result: dict[str, Any] = {}
        for prop in value.bl_rna.properties:
            if prop.identifier == "rna_type" or prop.is_readonly:
                continue
            try:
                encoded = to_jsonable(getattr(value, prop.identifier))
            except (AttributeError, TypeError, ValueError):
                continue
            if encoded is not SKIP:
                result[prop.identifier] = encoded
        return result
    try:
        items = [to_jsonable(item) for item in value]
    except TypeError:
        return SKIP
    if any(item is SKIP for item in items):
        return SKIP
    return items


def _resolve_id(rna_type: str, name: str):
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION" or prop.identifier == "rna_type":
            continue
        collection = getattr(bpy.data, prop.identifier, None)
        if collection is None or not hasattr(collection, "get"):
            continue
        candidate = collection.get(name)
        if candidate is not None and candidate.bl_rna.identifier == rna_type:
            return candidate
    return None


def from_jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [from_jsonable(item) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get("__type__")
    if marker == "set":
        return {from_jsonable(item) for item in value.get("items", [])}
    if marker == "id":
        return _resolve_id(value.get("rna_type", ""), value.get("name", ""))
    return {key: from_jsonable(item) for key, item in value.items()}


def operator_properties(operator: bpy.types.Operator) -> dict[str, Any]:
    if getattr(operator, "macros", None):
        return {
            key: operator_properties(item)
            for key, item in operator.macros.items()
        }

    props = getattr(operator, "properties", None)
    if props is None or not hasattr(props, "bl_rna"):
        return {}
    encoded = to_jsonable(props)
    return encoded if isinstance(encoded, dict) else {}


def operator_fingerprint(operator: bpy.types.Operator) -> str:
    return stable_digest(
        {
            "idname": operator.bl_idname,
            "properties": operator_properties(operator),
        }
    )
