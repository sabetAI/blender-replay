"""Run with: blender --background --factory-startup --python scripts/test_blender.py"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "blender_chronicle_test"


def load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


addon = load_addon()
addon.register()

from blender_chronicle_test import playback, runtime, state, storage  # noqa: E402

try:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "Recorded Cube"

    checkpoint = state.capture_mesh_state(cube, 1000)
    expected_digest = checkpoint["digest"]
    cube.data.vertices[0].co.x += 10.0
    cube.data.update()
    assert state.current_mesh_digest(cube) != expected_digest
    assert state.apply_mesh_state(bpy.context, checkpoint) == []
    assert state.current_mesh_digest(cube) == expected_digest

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(cube.data)
    for face in bm.faces:
        face.select = False
    bm.faces.ensure_lookup_table()
    bm.faces[0].select = True
    bmesh.update_edit_mesh(cube.data)
    context_state = state.capture_context(bpy.context)
    assert context_state["mesh_selection"]["faces"] == [0]
    bpy.ops.object.mode_set(mode="OBJECT")

    settings = bpy.context.scene.chronicle_settings
    settings.recording_name = "Blender smoke test"
    settings.max_checkpoint_vertices = 1000
    runtime.start(bpy.context)

    material = bpy.data.materials.new("Recorded Material")
    material.diffuse_color = (0.15, 0.35, 0.8, 1.0)
    material.roughness = 0.65
    material.use_nodes = True
    principled = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    principled.inputs["Base Color"].default_value = material.diffuse_color
    principled.inputs["Roughness"].default_value = material.roughness
    cube.data.materials.append(material)

    world = bpy.context.scene.world
    world.use_nodes = True
    background = next(node for node in world.node_tree.nodes if node.type == "BACKGROUND")
    background.inputs["Color"].default_value = (0.8, 0.25, 0.1, 1.0)
    background.inputs["Strength"].default_value = 0.35

    camera_data = bpy.data.cameras.new("Recorded Camera Data")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 12.0
    camera = bpy.data.objects.new("Recorded Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (7.0, -7.0, 6.0)
    bpy.context.scene.camera = camera

    light_data = bpy.data.lights.new("Recorded Light Data", "AREA")
    light_data.energy = 800.0
    light_data.color = (1.0, 0.7, 0.5)
    light = bpy.data.objects.new("Recorded Light", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (3.0, -4.0, 8.0)
    bpy.context.scene.render.resolution_x = 640
    bpy.context.scene.render.resolution_y = 360

    cube.location.x = 3.0
    bpy.context.view_layer.update()
    runtime.poll(bpy.context)
    paused_session = runtime.pause(bpy.context)
    paused_event_count = len(paused_session["events"])
    assert runtime.is_paused(bpy.context)

    bpy.ops.mesh.primitive_uv_sphere_add(location=(4.0, 0.0, 0.0))
    paused_bridge = bpy.context.active_object
    paused_bridge.name = "Paused Segment Bridge"
    cube.location.x = 7.0
    bpy.context.view_layer.update()
    runtime.poll(bpy.context)
    assert len(paused_session["events"]) == paused_event_count

    # A saved paused recording can resume after Blender reloads the runtime state.
    runtime.load_pre_handler("")
    assert not runtime.is_recording()
    assert runtime.can_resume(bpy.context)
    resumed_session = runtime.resume(bpy.context)
    assert runtime.is_recording()
    assert not runtime.is_paused(bpy.context)
    assert len(resumed_session["segments"]) == 2

    cube.location.x = 9.0
    bpy.context.view_layer.update()
    runtime.poll(bpy.context)
    session = runtime.stop(bpy.context)
    assert any(event["type"] == "checkpoint" for event in session["events"])
    assert session["recording_state"] == "stopped"
    assert len(session["segments"]) == 2
    assert all("ended_at" in segment for segment in session["segments"])
    operator_ids = {
        event["idname"] for event in session["events"] if event.get("type") == "operator"
    }
    assert "MESH_OT_primitive_uv_sphere_add" not in operator_ids
    assert storage.read_session(bpy.context)["name"] == "Blender smoke test"

    cube.data.materials.clear()
    bpy.data.materials.remove(material)
    cube.location.x = 20.0
    result = playback.play(
        bpy.context,
        session,
        restore_baseline=True,
        use_checkpoints=True,
    )
    assert abs(bpy.data.objects["Recorded Cube"].location.x - 9.0) < 1e-6
    assert bpy.data.objects.get("Paused Segment Bridge") is not None
    assert bpy.data.materials.get("Recorded Material") is not None
    assert bpy.data.objects["Recorded Cube"].data.materials[0].name == "Recorded Material"
    assert bpy.context.scene.camera.name == "Recorded Camera"
    assert bpy.data.objects["Recorded Camera"].data.ortho_scale == 12.0
    assert bpy.data.objects["Recorded Light"].data.energy == 800.0
    assert bpy.context.scene.render.resolution_x == 640
    assert result["warnings"] == []
    assert result["segments"] == 2

    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "smoke.chronicle.json")
        storage.export_session(path, session)
        assert storage.import_session(path)["name"] == "Blender smoke test"

    event = {
        "idname": "TRANSFORM_OT_translate",
        "properties": {"value": [1.0, 0.0, 0.0], "orient_type": "GLOBAL"},
        "area_type": None,
        "region_type": None,
    }
    cube = bpy.data.objects["Recorded Cube"]
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    before = cube.location.x
    assert playback.execute_operator(bpy.context, event) is None
    assert abs(cube.location.x - before - 1.0) < 1e-6
finally:
    addon.unregister()

print("CHRONICLE_BLENDER_TEST_OK")
