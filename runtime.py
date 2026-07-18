# SPDX-License-Identifier: GPL-3.0-or-later
"""Global recorder service for operator, context, and checkpoint capture."""

from __future__ import annotations

import time
from typing import Any

import bpy
from bpy.app.handlers import persistent

from . import storage
from .core import new_session
from .serialization import operator_fingerprint, operator_properties
from .state import (
    capture_context,
    capture_material_state,
    capture_mesh_state,
    capture_object_state,
    capture_scene_state,
    capture_world_state,
    context_signature,
    material_state_signature,
    object_state_signature,
)

POLL_INTERVAL = 0.08
AUTOSAVE_INTERVAL = 1.5
PENDING_STATE_GRACE = 1.0
OWN_OPERATOR_PREFIX = "CHRONICLE_OT_"

_active = False
_paused = False
_capture_guard = False
_session: dict[str, Any] | None = None
_started_at = 0.0
_time_offset = 0.0
_last_saved_at = 0.0
_dirty = False
_seen_operators: dict[int, str] = {}
_operator_event_indices: dict[int, int] = {}
_last_context_signature = ""
_known_objects: set[str] = set()
_object_signatures: dict[str, str] = {}
_material_signatures: dict[str, str] = {}
_world_signature = ""
_scene_signature = ""
_updated_mesh_names: set[str] = set()
_warned_meshes: set[str] = set()
_pending_state_start: int | None = None
_pending_state_changed_at = 0.0


def is_recording() -> bool:
    return _active


def is_paused(context: bpy.types.Context | None = None) -> bool:
    if _active:
        return _paused
    session = get_session(context) if context is not None else _session
    return bool(session and session.get("recording_state") == "paused")


def can_resume(context: bpy.types.Context) -> bool:
    return is_paused(context)


def get_session(context: bpy.types.Context | None = None) -> dict[str, Any] | None:
    if _session is not None:
        return _session
    if context is not None:
        return storage.read_session(context)
    return None


def set_session(context: bpy.types.Context, session: dict[str, Any]) -> None:
    global _session, _dirty
    _session = session
    _dirty = True
    storage.write_session(context, session)
    _dirty = False


def commit_pending_state() -> None:
    """Prevent the next operator from being reordered ahead of captured direct state edits."""
    global _pending_state_start
    _pending_state_start = None


def _relative_time() -> float:
    return round(_time_offset + max(0.0, time.monotonic() - _started_at), 4)


def _mark_dirty() -> None:
    global _dirty
    _dirty = True


def _append_event(event: dict[str, Any]) -> int:
    if _session is None:
        return -1
    event.setdefault("time", _relative_time())
    _session["events"].append(event)
    _mark_dirty()
    return len(_session["events"]) - 1


def _insert_event(index: int, event: dict[str, Any]) -> int:
    if _session is None:
        return -1
    event.setdefault("time", _relative_time())
    _session["events"].insert(index, event)
    for pointer, event_index in tuple(_operator_event_indices.items()):
        if event_index >= index:
            _operator_event_indices[pointer] = event_index + 1
    _mark_dirty()
    return index


def _append_state_event(event: dict[str, Any], pending: bool) -> int:
    global _pending_state_start, _pending_state_changed_at
    event_index = _append_event(event)
    if pending and event_index >= 0:
        if _pending_state_start is None:
            _pending_state_start = event_index
        _pending_state_changed_at = time.monotonic()
    return event_index


def _warn(message: str) -> None:
    if _session is None or message in _session["warnings"]:
        return
    _session["warnings"].append(message)
    _mark_dirty()


def _start_segment(reason: str) -> None:
    if _session is None:
        return
    segments = _session.setdefault("segments", [])
    index = len(segments) + 1
    marker_index = _append_event(
        {
            "type": "segment",
            "phase": "start",
            "segment": index,
            "reason": reason,
        }
    )
    segments.append(
        {
            "index": index,
            "started_at": _relative_time(),
            "start_event": marker_index + 1,
        }
    )
    _mark_dirty()


def _end_segment(reason: str) -> None:
    if _session is None:
        return
    segments = _session.setdefault("segments", [])
    if not segments or "ended_at" in segments[-1]:
        return
    index = segments[-1]["index"]
    marker_index = _append_event(
        {
            "type": "segment",
            "phase": "end",
            "segment": index,
            "reason": reason,
        }
    )
    segments[-1].update(
        {
            "ended_at": _relative_time(),
            "end_event": marker_index - 1,
            "end_reason": reason,
        }
    )
    _mark_dirty()


def _recorded_object_names(session: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for event in session.get("events", []):
        if event.get("type") != "checkpoint":
            continue
        if event.get("baseline") and "scene_objects" in event:
            names = set(event["scene_objects"])
        names.difference_update(event.get("deleted_objects", []))
        names.update(state["name"] for state in event.get("objects", []))
    return names


def _last_recorded_time(session: dict[str, Any]) -> float:
    return max(
        (float(event.get("time", 0.0)) for event in session.get("events", [])),
        default=0.0,
    )


def _operator_pointer(operator: bpy.types.Operator) -> int:
    try:
        return operator.as_pointer()
    except (AttributeError, ReferenceError):
        return id(operator)


def _is_own_operator(operator: bpy.types.Operator) -> bool:
    return operator.bl_idname.startswith(OWN_OPERATOR_PREFIX)


def _operator_event(context: bpy.types.Context, operator: bpy.types.Operator) -> dict[str, Any]:
    return {
        "type": "operator",
        "idname": operator.bl_idname,
        "label": operator.bl_label,
        "properties": operator_properties(operator),
        "area_type": context.area.type if context.area else None,
        "region_type": context.region.type if context.region else None,
    }


def _capture_context_event(
    context: bpy.types.Context,
    force: bool = False,
    pending: bool = False,
) -> None:
    global _last_context_signature
    snapshot = capture_context(context)
    signature = context_signature(snapshot)
    if force or signature != _last_context_signature:
        _append_state_event({"type": "context", "context": snapshot}, pending)
        _last_context_signature = signature


def _refresh_object_cache(context: bpy.types.Context) -> None:
    global _known_objects, _scene_signature, _world_signature
    _known_objects = {obj.name for obj in context.scene.objects}
    _object_signatures.clear()
    for obj in context.scene.objects:
        state = capture_object_state(obj)
        _object_signatures[obj.name] = object_state_signature(state)
    _material_signatures.clear()
    used_materials = {
        slot.material
        for obj in context.scene.objects
        for slot in obj.material_slots
        if slot.material is not None
    }
    for material in used_materials:
        state = capture_material_state(material)
        _material_signatures[material.name] = material_state_signature(state)
    world_state = capture_world_state(context.scene.world)
    _world_signature = material_state_signature(world_state) if world_state else ""
    _scene_signature = material_state_signature(capture_scene_state(context.scene))


def _capture_checkpoint(
    context: bpy.types.Context,
    *,
    baseline: bool = False,
    full: bool = False,
    reason: str = "change",
    pending: bool = False,
) -> None:
    global _known_objects, _scene_signature, _world_signature
    if _session is None:
        return
    settings = context.scene.chronicle_settings
    current_names = {obj.name for obj in context.scene.objects}
    deleted = sorted(_known_objects - current_names)
    object_states: list[dict[str, Any]] = []

    for obj in context.scene.objects:
        state = capture_object_state(obj)
        signature = object_state_signature(state)
        if (
            baseline
            or full
            or obj.name not in _object_signatures
            or _object_signatures[obj.name] != signature
        ):
            object_states.append(state)
        _object_signatures[obj.name] = signature
    for name in deleted:
        _object_signatures.pop(name, None)

    used_materials = {
        slot.material
        for obj in context.scene.objects
        for slot in obj.material_slots
        if slot.material is not None
    }
    material_states: list[dict[str, Any]] = []
    for material in sorted(used_materials, key=lambda item: item.name):
        state = capture_material_state(material)
        signature = material_state_signature(state)
        if (
            baseline
            or full
            or material.name not in _material_signatures
            or _material_signatures[material.name] != signature
        ):
            material_states.append(state)
        _material_signatures[material.name] = signature

    world_state = capture_world_state(context.scene.world)
    world_signature = material_state_signature(world_state) if world_state else ""
    changed_world = baseline or full or world_signature != _world_signature
    _world_signature = world_signature
    scene_state = capture_scene_state(context.scene)
    scene_signature = material_state_signature(scene_state)
    changed_scene = baseline or full or scene_signature != _scene_signature
    _scene_signature = scene_signature

    mesh_names = set(_updated_mesh_names)
    if baseline or full:
        mesh_names.update(obj.data.name for obj in context.scene.objects if obj.type == "MESH")
    active = context.view_layer.objects.active
    if active and active.type == "MESH" and active.mode == "EDIT":
        mesh_names.add(active.data.name)

    mesh_states: list[dict[str, Any]] = []
    if settings.geometry_checkpoints:
        for obj in context.scene.objects:
            if obj.type != "MESH" or obj.data.name not in mesh_names:
                continue
            state = capture_mesh_state(obj, settings.max_checkpoint_vertices)
            if state is None:
                if obj.data.name not in _warned_meshes:
                    _warned_meshes.add(obj.data.name)
                    _warn(
                        f"Geometry checkpoint skipped for {obj.name!r}: more than "
                        f"{settings.max_checkpoint_vertices:,} vertices"
                    )
                continue
            mesh_states.append(state)

    if (
        baseline
        or full
        or object_states
        or material_states
        or mesh_states
        or deleted
        or changed_world
        or changed_scene
    ):
        event: dict[str, Any] = {
            "type": "checkpoint",
            "reason": reason,
            "baseline": baseline,
            "segment_baseline": full,
            "objects": object_states,
            "materials": material_states,
            "meshes": mesh_states,
            "deleted_objects": deleted,
        }
        if changed_world:
            event["world"] = world_state
        if changed_scene:
            event["scene"] = scene_state
        if baseline:
            event["scene_objects"] = sorted(current_names)
        _append_state_event(event, pending)
    _known_objects = current_names
    _updated_mesh_names.clear()


def poll(context: bpy.types.Context | None = None) -> None:
    global _capture_guard, _last_saved_at, _pending_state_start
    if not _active or _paused or _capture_guard:
        return
    context = context or bpy.context
    if context.window_manager is None or context.scene is None:
        return
    _capture_guard = True
    try:
        if (
            _pending_state_start is not None
            and time.monotonic() - _pending_state_changed_at >= PENDING_STATE_GRACE
        ):
            _pending_state_start = None

        new_operators: list[tuple[int, str, bpy.types.Operator]] = []
        current_pointers: set[int] = set()
        for operator in context.window_manager.operators:
            pointer = _operator_pointer(operator)
            current_pointers.add(pointer)
            fingerprint = operator_fingerprint(operator)
            if _is_own_operator(operator):
                _seen_operators[pointer] = fingerprint
                continue
            previous = _seen_operators.get(pointer)
            if previous is None:
                new_operators.append((pointer, fingerprint, operator))
            elif previous != fingerprint:
                event_index = _operator_event_indices.get(pointer)
                if _session is not None and event_index is not None and event_index >= 0:
                    event = _session["events"][event_index]
                    event["properties"] = operator_properties(operator)
                    event["adjusted_at"] = _relative_time()
                    _mark_dirty()
                _seen_operators[pointer] = fingerprint

        for pointer in set(_seen_operators) - current_pointers:
            _seen_operators.pop(pointer, None)
            _operator_event_indices.pop(pointer, None)

        insertion_index = _pending_state_start
        for pointer, fingerprint, operator in new_operators:
            event = _operator_event(context, operator)
            if insertion_index is None:
                event_index = _append_event(event)
            else:
                event_index = _insert_event(insertion_index, event)
                insertion_index += 1
            _operator_event_indices[pointer] = event_index
            _seen_operators[pointer] = fingerprint
        new_operator = bool(new_operators)
        if new_operator:
            _pending_state_start = None

        _capture_checkpoint(
            context,
            reason="operator" if new_operator else "state",
            pending=not new_operator,
        )
        _capture_context_event(
            context,
            force=new_operator,
            pending=not new_operator,
        )
        if _dirty and time.monotonic() - _last_saved_at >= AUTOSAVE_INTERVAL:
            save(context)
    finally:
        _capture_guard = False


def _timer() -> float | None:
    if not _active:
        return None
    if not _paused:
        try:
            poll(bpy.context)
        except Exception as exc:  # Keep the recorder alive and expose the failure.
            _warn(f"Recorder polling error: {type(exc).__name__}: {exc}")
    return POLL_INTERVAL


def _ensure_timer() -> None:
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=POLL_INTERVAL, persistent=False)


def start(context: bpy.types.Context) -> dict[str, Any]:
    global _active, _paused, _session, _started_at, _time_offset, _last_saved_at
    global _last_context_signature, _dirty, _pending_state_start
    if _active or can_resume(context):
        raise RuntimeError("Finish or discard the paused recording before starting a new one")
    settings = context.scene.chronicle_settings
    _session = new_session(
        settings.recording_name,
        bpy.app.version_string,
        context.scene.name,
        settings.max_checkpoint_vertices,
    )
    _started_at = time.monotonic()
    _time_offset = 0.0
    _last_saved_at = 0.0
    _last_context_signature = ""
    _seen_operators.clear()
    _operator_event_indices.clear()
    _updated_mesh_names.clear()
    _warned_meshes.clear()
    _pending_state_start = None
    for operator in context.window_manager.operators:
        _seen_operators[_operator_pointer(operator)] = operator_fingerprint(operator)
    _refresh_object_cache(context)
    _active = True
    _paused = False
    context.window_manager.chronicle_is_recording = True
    context.window_manager.chronicle_is_paused = False
    _start_segment("recording_start")
    _capture_checkpoint(context, baseline=True, reason="recording_start")
    _capture_context_event(context, force=True)
    _dirty = True
    save(context)
    _ensure_timer()
    return _session


def pause(context: bpy.types.Context) -> dict[str, Any]:
    global _paused, _time_offset
    if not _active or _paused or _session is None:
        raise RuntimeError("Blender Replay is not actively capturing")
    poll(context)
    commit_pending_state()
    _end_segment("pause")
    _time_offset = _relative_time()
    _paused = True
    _session["recording_state"] = "paused"
    context.window_manager.chronicle_is_paused = True
    _mark_dirty()
    save(context)
    return _session


def resume(context: bpy.types.Context) -> dict[str, Any]:
    global _active, _paused, _session, _started_at, _time_offset
    global _last_context_signature, _dirty, _pending_state_start
    global _known_objects, _world_signature, _scene_signature

    session = get_session(context)
    if session is None or session.get("recording_state") != "paused":
        raise RuntimeError("No paused Blender Replay recording is available")
    _session = session
    _active = True
    _paused = False
    _started_at = time.monotonic()
    _time_offset = _last_recorded_time(session)
    _last_context_signature = ""
    _pending_state_start = None
    _known_objects = _recorded_object_names(session)
    _object_signatures.clear()
    _material_signatures.clear()
    _world_signature = ""
    _scene_signature = ""
    _seen_operators.clear()
    _operator_event_indices.clear()
    _updated_mesh_names.clear()
    _warned_meshes.clear()
    for operator in context.window_manager.operators:
        _seen_operators[_operator_pointer(operator)] = operator_fingerprint(operator)
    context.window_manager.chronicle_is_recording = True
    context.window_manager.chronicle_is_paused = False
    _session["recording_state"] = "recording"
    _start_segment("recording_resume")
    _capture_checkpoint(context, full=True, reason="recording_resume")
    _capture_context_event(context, force=True)
    _dirty = True
    save(context)
    _ensure_timer()
    return _session


def save(context: bpy.types.Context) -> None:
    global _dirty, _last_saved_at
    if _session is None:
        return
    storage.write_session(context, _session)
    _dirty = False
    _last_saved_at = time.monotonic()


def stop(context: bpy.types.Context) -> dict[str, Any] | None:
    global _active, _paused, _session
    if not _active:
        session = get_session(context)
        if session is None or session.get("recording_state") != "paused":
            return session
        _session = session
    elif not _paused:
        poll(context)
        commit_pending_state()
        _end_segment("stop")
    _active = False
    _paused = False
    context.window_manager.chronicle_is_recording = False
    context.window_manager.chronicle_is_paused = False
    if _session is not None:
        _session["recording_state"] = "stopped"
        _session["stopped_at"] = time.time()
        _mark_dirty()
    save(context)
    return _session


def discard(context: bpy.types.Context) -> None:
    global _active, _paused, _session, _dirty
    _active = False
    _paused = False
    context.window_manager.chronicle_is_recording = False
    context.window_manager.chronicle_is_paused = False
    text = bpy.data.texts.get(context.scene.chronicle_session_text)
    if text is not None:
        bpy.data.texts.remove(text)
    context.scene.chronicle_session_text = ""
    _session = None
    _dirty = False


@persistent
def depsgraph_update_handler(scene, depsgraph) -> None:
    if not _active or _paused or _capture_guard:
        return
    for update in depsgraph.updates:
        updated_id = getattr(update.id, "original", update.id)
        if isinstance(updated_id, bpy.types.Mesh):
            _updated_mesh_names.add(updated_id.name)
        elif isinstance(updated_id, bpy.types.Object) and updated_id.type == "MESH":
            _updated_mesh_names.add(updated_id.data.name)


@persistent
def load_pre_handler(_filepath) -> None:
    global _active, _paused, _session, _dirty, _pending_state_start
    _active = False
    _paused = False
    _session = None
    _dirty = False
    _pending_state_start = None
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is not None:
        window_manager.chronicle_is_recording = False
        window_manager.chronicle_is_paused = False


def register() -> None:
    if depsgraph_update_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(depsgraph_update_handler)
    if load_pre_handler not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(load_pre_handler)


def unregister() -> None:
    global _active, _paused
    _active = False
    _paused = False
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if depsgraph_update_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update_handler)
    if load_pre_handler in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(load_pre_handler)
