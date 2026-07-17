# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay recorded Blender sessions without evaluating source code."""

from __future__ import annotations

from typing import Any

import bpy

from .core import operator_identifier_to_path, validate_session
from .serialization import from_jsonable
from .state import (
    apply_context,
    apply_material_state,
    apply_mesh_state,
    apply_object_state,
    apply_scene_state,
    apply_world_state,
    current_mesh_digest,
)

BLOCKED_OPERATORS = {
    "WM_OT_quit_blender",
    "WM_OT_open_mainfile",
    "WM_OT_read_factory_settings",
    "WM_OT_read_homefile",
    "SCRIPT_OT_python_file_run",
}


def _area_override(context: bpy.types.Context, event: dict[str, Any]):
    if context.window is None or context.window.screen is None:
        return {}
    requested_area = event.get("area_type")
    area = next(
        (item for item in context.window.screen.areas if item.type == requested_area),
        context.area,
    )
    if area is None:
        return {}
    requested_region = event.get("region_type") or "WINDOW"
    region = next(
        (item for item in area.regions if item.type == requested_region),
        next((item for item in area.regions if item.type == "WINDOW"), None),
    )
    override = {"window": context.window, "area": area}
    if region is not None:
        override["region"] = region
    return override


def execute_operator(context: bpy.types.Context, event: dict[str, Any]) -> str | None:
    identifier = event["idname"]
    if identifier in BLOCKED_OPERATORS or identifier.startswith("CHRONICLE_OT_"):
        return f"Blocked unsafe/internal operator {identifier}"
    try:
        namespace, name = operator_identifier_to_path(identifier)
        operator = getattr(getattr(bpy.ops, namespace), name)
    except (ValueError, AttributeError) as exc:
        return f"Operator {identifier} is unavailable: {exc}"
    properties = from_jsonable(event.get("properties", {}))
    override = _area_override(context, event)
    try:
        if override:
            with context.temp_override(**override):
                if not operator.poll():
                    return f"Operator {identifier} failed its context poll"
                result = operator("EXEC_DEFAULT", **properties)
        else:
            if not operator.poll():
                return f"Operator {identifier} failed its context poll"
            result = operator("EXEC_DEFAULT", **properties)
    except Exception as exc:
        return f"Operator {identifier} failed: {type(exc).__name__}: {exc}"
    if "CANCELLED" in result:
        return f"Operator {identifier} was cancelled"
    return None


def _apply_checkpoint(
    context: bpy.types.Context,
    event: dict[str, Any],
    use_geometry: bool,
) -> tuple[int, list[str]]:
    repaired = 0
    warnings: list[str] = []
    for material_state in event.get("materials", []):
        _material, material_warnings = apply_material_state(material_state)
        warnings.extend(material_warnings)
    if "world" in event:
        warnings.extend(apply_world_state(context, event.get("world")))
    for object_state in event.get("objects", []):
        _obj, object_warnings = apply_object_state(context, object_state)
        warnings.extend(object_warnings)
    # Parents may be ordered after their children in the recording.
    for object_state in event.get("objects", []):
        obj = bpy.data.objects.get(object_state["name"])
        if obj is not None:
            parent_name = object_state.get("parent")
            obj.parent = bpy.data.objects.get(parent_name) if parent_name else None
    for name in event.get("deleted_objects", []):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.name in context.scene.objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    if "scene" in event:
        warnings.extend(apply_scene_state(context, event["scene"]))
    if use_geometry:
        for mesh_state in event.get("meshes", []):
            obj = bpy.data.objects.get(mesh_state["object"])
            differs = obj is None or obj.type != "MESH"
            if not differs:
                differs = current_mesh_digest(obj) != mesh_state.get("digest")
            mesh_warnings = apply_mesh_state(context, mesh_state, only_if_different=True)
            warnings.extend(mesh_warnings)
            if differs and not mesh_warnings:
                repaired += 1
    return repaired, warnings


def play(
    context: bpy.types.Context,
    session: dict[str, Any],
    *,
    restore_baseline: bool,
    use_checkpoints: bool,
) -> dict[str, Any]:
    validate_session(session)
    result = {"operators": 0, "repairs": 0, "warnings": []}

    events = session["events"]
    baseline_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("type") == "checkpoint" and event.get("baseline")
        ),
        None,
    )
    if restore_baseline and baseline_index is not None:
        baseline = events[baseline_index]
        baseline_names = set(baseline.get("scene_objects", []))
        for obj in list(context.scene.objects):
            if obj.name not in baseline_names:
                bpy.data.objects.remove(obj, do_unlink=True)
        repairs, warnings = _apply_checkpoint(
            context,
            baseline,
            use_geometry=use_checkpoints,
        )
        result["repairs"] += repairs
        result["warnings"].extend(warnings)

    for index, event in enumerate(events):
        event_type = event.get("type")
        if index == baseline_index and restore_baseline:
            continue
        if event_type == "operator":
            error = execute_operator(context, event)
            if error:
                result["warnings"].append(error)
            else:
                result["operators"] += 1
        elif event_type == "context":
            result["warnings"].extend(apply_context(context, event.get("context", {})))
        elif event_type == "checkpoint":
            repairs, warnings = _apply_checkpoint(
                context,
                event,
                use_geometry=use_checkpoints,
            )
            result["repairs"] += repairs
            result["warnings"].extend(warnings)
    context.view_layer.update()
    return result
