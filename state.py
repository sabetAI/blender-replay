# SPDX-License-Identifier: GPL-3.0-or-later
"""Context and scene checkpoints that make modeling replay deterministic."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from mathutils import Matrix

from .core import stable_digest
from .serialization import SKIP, from_jsonable, to_jsonable

MODE_TO_OPERATOR = {
    "EDIT_MESH": "EDIT",
    "EDIT_CURVE": "EDIT",
    "EDIT_SURFACE": "EDIT",
    "EDIT_TEXT": "EDIT",
    "EDIT_ARMATURE": "EDIT",
    "EDIT_METABALL": "EDIT",
    "EDIT_LATTICE": "EDIT",
    "POSE": "POSE",
    "SCULPT": "SCULPT",
    "PAINT_WEIGHT": "WEIGHT_PAINT",
    "PAINT_VERTEX": "VERTEX_PAINT",
    "PAINT_TEXTURE": "TEXTURE_PAINT",
    "OBJECT": "OBJECT",
}


def _matrix_rows(matrix) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def capture_context(context: bpy.types.Context) -> dict[str, Any]:
    active = context.view_layer.objects.active
    result: dict[str, Any] = {
        "mode": context.mode,
        "active_object": active.name if active else None,
        "selected_objects": sorted(obj.name for obj in context.selected_objects),
    }

    if active and active.type == "MESH" and active.mode == "EDIT":
        bm = bmesh.from_edit_mesh(active.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        result["mesh_selection"] = {
            "vertices": [vert.index for vert in bm.verts if vert.select],
            "edges": [edge.index for edge in bm.edges if edge.select],
            "faces": [face.index for face in bm.faces if face.select],
            "select_mode": list(context.tool_settings.mesh_select_mode),
        }
        active_element = bm.select_history.active
        if isinstance(active_element, bmesh.types.BMVert):
            result["mesh_selection"]["active"] = ["VERT", active_element.index]
        elif isinstance(active_element, bmesh.types.BMEdge):
            result["mesh_selection"]["active"] = ["EDGE", active_element.index]
        elif isinstance(active_element, bmesh.types.BMFace):
            result["mesh_selection"]["active"] = ["FACE", active_element.index]

    if active and active.type == "ARMATURE":
        bones = active.data.bones
        result["bone_selection"] = {
            "selected": sorted(bone.name for bone in bones if bone.select),
            "active": bones.active.name if bones.active else None,
        }
    return result


def context_signature(snapshot: dict[str, Any]) -> str:
    return stable_digest(snapshot)


def _set_active_object(context: bpy.types.Context, object_name: str | None):
    for obj in context.view_layer.objects:
        obj.select_set(False)
    active = bpy.data.objects.get(object_name) if object_name else None
    context.view_layer.objects.active = active
    return active


def apply_context(context: bpy.types.Context, snapshot: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    current = context.view_layer.objects.active
    if current and current.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError as exc:
            warnings.append(f"Could not leave {current.mode}: {exc}")

    active = _set_active_object(context, snapshot.get("active_object"))
    for name in snapshot.get("selected_objects", []):
        obj = bpy.data.objects.get(name)
        if obj and obj.name in context.view_layer.objects:
            obj.select_set(True)
    if snapshot.get("active_object") and active is None:
        warnings.append(f"Missing active object {snapshot['active_object']!r}")
        return warnings

    target_mode = MODE_TO_OPERATOR.get(snapshot.get("mode", "OBJECT"), "OBJECT")
    if active and target_mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode=target_mode)
        except RuntimeError as exc:
            warnings.append(f"Could not enter {target_mode} on {active.name!r}: {exc}")

    selection = snapshot.get("mesh_selection")
    if selection and active and active.type == "MESH" and active.mode == "EDIT":
        bm = bmesh.from_edit_mesh(active.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for element in (*bm.verts, *bm.edges, *bm.faces):
            element.select = False
        for index in selection.get("vertices", []):
            if index < len(bm.verts):
                bm.verts[index].select = True
        for index in selection.get("edges", []):
            if index < len(bm.edges):
                bm.edges[index].select = True
        for index in selection.get("faces", []):
            if index < len(bm.faces):
                bm.faces[index].select = True
        select_mode = selection.get("select_mode", [])
        if len(select_mode) == 3:
            context.tool_settings.mesh_select_mode = tuple(bool(item) for item in select_mode)
        bm.select_history.clear()
        active_element = selection.get("active")
        if active_element:
            kind, index = active_element
            sequence = {"VERT": bm.verts, "EDGE": bm.edges, "FACE": bm.faces}.get(kind)
            if sequence is not None and index < len(sequence):
                bm.select_history.add(sequence[index])
        bmesh.update_edit_mesh(active.data, loop_triangles=False, destructive=False)

    bone_selection = snapshot.get("bone_selection")
    if bone_selection and active and active.type == "ARMATURE":
        selected = set(bone_selection.get("selected", []))
        for bone in active.data.bones:
            bone.select = bone.name in selected
        active_bone_name = bone_selection.get("active")
        active.data.bones.active = (
            active.data.bones.get(active_bone_name) if active_bone_name else None
        )
    return warnings


def capture_object_state(obj: bpy.types.Object) -> dict[str, Any]:
    state: dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "data_name": obj.data.name if obj.data else None,
        "matrix_world": _matrix_rows(obj.matrix_world),
        "transform": {
            "location": list(obj.location),
            "rotation_mode": obj.rotation_mode,
            "rotation_euler": list(obj.rotation_euler),
            "rotation_quaternion": list(obj.rotation_quaternion),
            "rotation_axis_angle": list(obj.rotation_axis_angle),
            "scale": list(obj.scale),
            "delta_location": list(obj.delta_location),
            "delta_rotation_euler": list(obj.delta_rotation_euler),
            "delta_rotation_quaternion": list(obj.delta_rotation_quaternion),
            "delta_scale": list(obj.delta_scale),
            "matrix_parent_inverse": _matrix_rows(obj.matrix_parent_inverse),
        },
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
        "display_type": obj.display_type,
        "parent": obj.parent.name if obj.parent else None,
        "parent_type": obj.parent_type,
        "parent_bone": obj.parent_bone,
        "color": list(obj.color),
    }
    if obj.data and hasattr(obj.data, "materials"):
        state["material_slots"] = [
            material.name if material else None for material in obj.data.materials
        ]
    if obj.type == "CAMERA":
        state["camera"] = {
            "type": obj.data.type,
            "lens": obj.data.lens,
            "ortho_scale": obj.data.ortho_scale,
            "clip_start": obj.data.clip_start,
            "clip_end": obj.data.clip_end,
            "dof_use_dof": obj.data.dof.use_dof,
            "dof_focus_distance": obj.data.dof.focus_distance,
            "dof_aperture_fstop": obj.data.dof.aperture_fstop,
        }
    elif obj.type == "LIGHT":
        state["light"] = {
            "type": obj.data.type,
            "energy": obj.data.energy,
            "color": list(obj.data.color),
            "shadow_soft_size": obj.data.shadow_soft_size,
        }
        if hasattr(obj.data, "angle"):
            state["light"]["angle"] = obj.data.angle
    state["modifiers"] = []
    for modifier in obj.modifiers:
        values: dict[str, Any] = {
            "name": modifier.name,
            "type": modifier.type,
            "show_viewport": modifier.show_viewport,
            "show_render": modifier.show_render,
        }
        for prop in modifier.bl_rna.properties:
            if (
                prop.identifier in values
                or prop.identifier == "rna_type"
                or prop.is_readonly
                or prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
            ):
                continue
            try:
                value = getattr(modifier, prop.identifier)
                encoded = to_jsonable(value)
                if encoded is not SKIP:
                    values[prop.identifier] = encoded
            except (AttributeError, TypeError, ValueError):
                continue
        state["modifiers"].append(values)
    return state


def capture_material_state(material: bpy.types.Material) -> dict[str, Any]:
    state: dict[str, Any] = {
        "name": material.name,
        "diffuse_color": list(material.diffuse_color),
        "metallic": material.metallic,
        "roughness": material.roughness,
        "use_nodes": material.use_nodes,
        "nodes": [],
    }
    if material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            inputs: dict[str, Any] = {}
            for socket in node.inputs:
                if not hasattr(socket, "default_value"):
                    continue
                value = to_jsonable(socket.default_value)
                if value is not SKIP:
                    inputs[socket.identifier] = value
            if inputs:
                state["nodes"].append(
                    {
                        "name": node.name,
                        "bl_idname": node.bl_idname,
                        "inputs": inputs,
                    }
                )
    return state


def material_state_signature(state: dict[str, Any]) -> str:
    return stable_digest(state)


def apply_material_state(state: dict[str, Any]) -> tuple[bpy.types.Material, list[str]]:
    warnings: list[str] = []
    material = bpy.data.materials.get(state["name"])
    if material is None:
        material = bpy.data.materials.new(state["name"])
    for key in ("diffuse_color", "metallic", "roughness"):
        try:
            setattr(material, key, state[key])
        except (AttributeError, KeyError, TypeError, ValueError):
            warnings.append(f"Could not restore material {material.name}.{key}")
    material.use_nodes = state.get("use_nodes", False)
    if material.use_nodes and material.node_tree:
        for node_state in state.get("nodes", []):
            node = material.node_tree.nodes.get(node_state["name"])
            if node is None:
                node = next(
                    (
                        item
                        for item in material.node_tree.nodes
                        if item.bl_idname == node_state["bl_idname"]
                    ),
                    None,
                )
            if node is None:
                try:
                    node = material.node_tree.nodes.new(node_state["bl_idname"])
                    node.name = node_state["name"]
                except RuntimeError:
                    warnings.append(f"Could not create node {node_state['name']!r}")
                    continue
            for identifier, value in node_state.get("inputs", {}).items():
                socket = node.inputs.get(identifier)
                if socket is None or not hasattr(socket, "default_value"):
                    continue
                try:
                    socket.default_value = from_jsonable(value)
                except (TypeError, ValueError):
                    warnings.append(
                        f"Could not restore {material.name}.{node.name}.{identifier}"
                    )
    return material, warnings


def capture_world_state(world: bpy.types.World | None) -> dict[str, Any] | None:
    if world is None:
        return None
    state: dict[str, Any] = {
        "name": world.name,
        "color": list(world.color),
        "use_nodes": world.use_nodes,
        "background": {},
    }
    if world.use_nodes and world.node_tree:
        background = next(
            (node for node in world.node_tree.nodes if node.type == "BACKGROUND"),
            None,
        )
        if background:
            for socket in background.inputs:
                if hasattr(socket, "default_value"):
                    value = to_jsonable(socket.default_value)
                    if value is not SKIP:
                        state["background"][socket.identifier] = value
    return state


def apply_world_state(context: bpy.types.Context, state: dict[str, Any] | None) -> list[str]:
    if state is None:
        return []
    warnings: list[str] = []
    world = bpy.data.worlds.get(state["name"])
    if world is None:
        world = bpy.data.worlds.new(state["name"])
    context.scene.world = world
    world.color = state.get("color", world.color)
    world.use_nodes = state.get("use_nodes", False)
    if world.use_nodes and world.node_tree:
        background = next(
            (node for node in world.node_tree.nodes if node.type == "BACKGROUND"),
            None,
        )
        if background:
            for identifier, value in state.get("background", {}).items():
                socket = background.inputs.get(identifier)
                if socket is None or not hasattr(socket, "default_value"):
                    continue
                try:
                    socket.default_value = from_jsonable(value)
                except (TypeError, ValueError):
                    warnings.append(f"Could not restore world background {identifier}")
    return warnings


def capture_scene_state(scene: bpy.types.Scene) -> dict[str, Any]:
    return {
        "camera": scene.camera.name if scene.camera else None,
        "render_engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "film_transparent": scene.render.film_transparent,
        "view_transform": scene.view_settings.view_transform,
        "look": scene.view_settings.look,
        "exposure": scene.view_settings.exposure,
    }


def apply_scene_state(context: bpy.types.Context, state: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    scene = context.scene
    camera_name = state.get("camera")
    scene.camera = bpy.data.objects.get(camera_name) if camera_name else None
    assignments = {
        "engine": state.get("render_engine"),
        "resolution_x": state.get("resolution_x"),
        "resolution_y": state.get("resolution_y"),
        "resolution_percentage": state.get("resolution_percentage"),
        "film_transparent": state.get("film_transparent"),
    }
    for key, value in assignments.items():
        if value is None:
            continue
        try:
            setattr(scene.render, key, value)
        except (AttributeError, TypeError, ValueError):
            warnings.append(f"Could not restore scene.render.{key}")
    for key in ("view_transform", "look", "exposure"):
        if key not in state:
            continue
        try:
            setattr(scene.view_settings, key, state[key])
        except (AttributeError, TypeError, ValueError):
            warnings.append(f"Could not restore scene.view_settings.{key}")
    return warnings


def object_state_signature(state: dict[str, Any]) -> str:
    return stable_digest(state)


def apply_object_state(
    context: bpy.types.Context,
    state: dict[str, Any],
) -> tuple[bpy.types.Object | None, list[str]]:
    warnings: list[str] = []
    obj = bpy.data.objects.get(state["name"])
    if obj is None:
        if state.get("type") == "MESH":
            mesh = bpy.data.meshes.get(state.get("data_name", ""))
            mesh = mesh or bpy.data.meshes.new(state.get("data_name") or f"{state['name']}Mesh")
            obj = bpy.data.objects.new(state["name"], mesh)
        elif state.get("type") == "CAMERA":
            camera = bpy.data.cameras.get(state.get("data_name", ""))
            camera = camera or bpy.data.cameras.new(
                state.get("data_name") or f"{state['name']}Camera"
            )
            obj = bpy.data.objects.new(state["name"], camera)
        elif state.get("type") == "LIGHT":
            light = bpy.data.lights.get(state.get("data_name", ""))
            light_type = state.get("light", {}).get("type", "POINT")
            light = light or bpy.data.lights.new(
                state.get("data_name") or f"{state['name']}Light",
                light_type,
            )
            obj = bpy.data.objects.new(state["name"], light)
        elif state.get("type") == "EMPTY":
            obj = bpy.data.objects.new(state["name"], None)
        else:
            warnings.append(
                f"Cannot reconstruct missing {state.get('type')} object {state['name']!r}"
            )
            return None, warnings
        context.scene.collection.objects.link(obj)
    elif obj.type != state.get("type"):
        warnings.append(
            f"Object {obj.name!r} has type {obj.type}, expected {state.get('type')}"
        )
        return obj, warnings

    if obj.name not in context.scene.objects:
        context.scene.collection.objects.link(obj)

    object_properties = (
        "hide_viewport",
        "hide_render",
        "display_type",
        "parent_type",
        "parent_bone",
        "color",
    )
    for key in object_properties:
        if key in state:
            try:
                setattr(obj, key, state[key])
            except (AttributeError, TypeError, ValueError):
                warnings.append(f"Could not restore {obj.name}.{key}")
    parent_name = state.get("parent")
    obj.parent = bpy.data.objects.get(parent_name) if parent_name else None

    transform = state.get("transform")
    if transform:
        obj.rotation_mode = transform["rotation_mode"]
        obj.location = transform["location"]
        obj.scale = transform["scale"]
        obj.delta_location = transform["delta_location"]
        obj.delta_scale = transform["delta_scale"]
        if obj.rotation_mode == "QUATERNION":
            obj.rotation_quaternion = transform["rotation_quaternion"]
            obj.delta_rotation_quaternion = transform["delta_rotation_quaternion"]
        elif obj.rotation_mode == "AXIS_ANGLE":
            obj.rotation_axis_angle = transform["rotation_axis_angle"]
            obj.delta_rotation_euler = transform["delta_rotation_euler"]
        else:
            obj.rotation_euler = transform["rotation_euler"]
            obj.delta_rotation_euler = transform["delta_rotation_euler"]
        obj.matrix_parent_inverse = Matrix(transform["matrix_parent_inverse"])
    else:
        obj.matrix_world = Matrix(state["matrix_world"])

    if obj.data and hasattr(obj.data, "materials") and "material_slots" in state:
        obj.data.materials.clear()
        for material_name in state["material_slots"]:
            if not material_name:
                continue
            material = bpy.data.materials.get(material_name)
            if material:
                obj.data.materials.append(material)

    for data_key in ("camera", "light"):
        for key, value in state.get(data_key, {}).items():
            if key == "type" and data_key == "light" and obj.data.type != value:
                continue
            if key.startswith("dof_") and data_key == "camera":
                dof_key = key.removeprefix("dof_")
                try:
                    setattr(obj.data.dof, dof_key, value)
                except (AttributeError, TypeError, ValueError):
                    warnings.append(f"Could not restore {obj.name}.dof.{dof_key}")
                continue
            try:
                setattr(obj.data, key, value)
            except (AttributeError, TypeError, ValueError):
                warnings.append(f"Could not restore {obj.name}.data.{key}")

    for modifier_state in state.get("modifiers", []):
        modifier = obj.modifiers.get(modifier_state["name"])
        if modifier is None:
            try:
                modifier = obj.modifiers.new(modifier_state["name"], modifier_state["type"])
            except RuntimeError:
                warnings.append(
                    f"Could not create {modifier_state['type']} modifier on {obj.name!r}"
                )
                continue
        for key, value in modifier_state.items():
            if key in {"name", "type"}:
                continue
            try:
                setattr(modifier, key, from_jsonable(value))
            except (AttributeError, TypeError, ValueError):
                continue
    return obj, warnings


def capture_mesh_state(obj: bpy.types.Object, max_vertices: int) -> dict[str, Any] | None:
    if obj.type != "MESH":
        return None
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        if len(bm.verts) > max_vertices:
            return None
        state = {
            "object": obj.name,
            "mesh": obj.data.name,
            "vertices": [list(vert.co) for vert in bm.verts],
            "edges": [[edge.verts[0].index, edge.verts[1].index] for edge in bm.edges],
            "faces": [[vert.index for vert in face.verts] for face in bm.faces],
            "vertex_select": [vert.index for vert in bm.verts if vert.select],
            "edge_select": [edge.index for edge in bm.edges if edge.select],
            "face_select": [face.index for face in bm.faces if face.select],
            "face_material": [face.material_index for face in bm.faces],
            "face_smooth": [face.smooth for face in bm.faces],
        }
    else:
        mesh = obj.data
        if len(mesh.vertices) > max_vertices:
            return None
        state = {
            "object": obj.name,
            "mesh": mesh.name,
            "vertices": [list(vertex.co) for vertex in mesh.vertices],
            "edges": [[vertex for vertex in edge.vertices] for edge in mesh.edges],
            "faces": [[vertex for vertex in polygon.vertices] for polygon in mesh.polygons],
            "vertex_select": [vertex.index for vertex in mesh.vertices if vertex.select],
            "edge_select": [edge.index for edge in mesh.edges if edge.select],
            "face_select": [polygon.index for polygon in mesh.polygons if polygon.select],
            "face_material": [polygon.material_index for polygon in mesh.polygons],
            "face_smooth": [polygon.use_smooth for polygon in mesh.polygons],
        }
    state["digest"] = mesh_state_digest(state)
    return state


def mesh_state_digest(state: dict[str, Any]) -> str:
    return stable_digest(
        {
            "vertices": [[round(value, 8) for value in co] for co in state["vertices"]],
            "edges": state["edges"],
            "faces": state["faces"],
        }
    )


def current_mesh_digest(obj: bpy.types.Object) -> str | None:
    state = capture_mesh_state(obj, max_vertices=2**31 - 1)
    return state["digest"] if state else None


def apply_mesh_state(
    context: bpy.types.Context,
    state: dict[str, Any],
    only_if_different: bool = True,
) -> list[str]:
    obj = bpy.data.objects.get(state["object"])
    if obj is None or obj.type != "MESH":
        return [f"Missing mesh object {state['object']!r} for checkpoint"]
    if only_if_different and current_mesh_digest(obj) == state.get("digest"):
        return []

    previous_active = context.view_layer.objects.active
    previous_mode = previous_active.mode if previous_active else "OBJECT"
    if previous_active and previous_active.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    context.view_layer.objects.active = obj
    obj.select_set(True)
    if obj.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError as exc:
            return [f"Could not prepare {obj.name!r} for geometry checkpoint: {exc}"]

    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(state["vertices"], state["edges"], state["faces"])
    mesh.update()
    selected_vertices = set(state.get("vertex_select", []))
    selected_edges = set(state.get("edge_select", []))
    selected_faces = set(state.get("face_select", []))
    for vertex in mesh.vertices:
        vertex.select = vertex.index in selected_vertices
    for edge in mesh.edges:
        edge.select = edge.index in selected_edges
    for polygon in mesh.polygons:
        polygon.select = polygon.index in selected_faces
        if polygon.index < len(state.get("face_material", [])):
            polygon.material_index = state["face_material"][polygon.index]
        if polygon.index < len(state.get("face_smooth", [])):
            polygon.use_smooth = state["face_smooth"][polygon.index]

    if previous_active:
        context.view_layer.objects.active = previous_active
        if previous_mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode=MODE_TO_OPERATOR.get(previous_mode, previous_mode))
            except RuntimeError:
                pass
    return []
