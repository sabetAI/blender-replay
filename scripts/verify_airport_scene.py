"""Build, record, replay, and render a low-poly airport diorama."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "verification" / "airport_scene"
OUTPUT.mkdir(parents=True, exist_ok=True)
PACKAGE_NAME = "blender_chronicle_airport_test"


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

from blender_chronicle_airport_test import playback, runtime, state, storage  # noqa: E402
from blender_chronicle_airport_test.core import stable_digest  # noqa: E402


@dataclass
class BuildAction:
    operator: str
    properties: dict[str, Any]
    configure: Callable[[bpy.types.Object], None]


MATERIALS: dict[str, bpy.types.Material] = {}
ACTIONS: list[BuildAction] = []
BUILD_INDEX = 0
BUILD_PHASE = "warmup"
KEYMAP_ITEM = None
KEYMAP_OPERATOR = ""
OBJECTS_BEFORE: set[int] = set()


def material(name: str, color: tuple[float, float, float, float], roughness=0.72):
    result = bpy.data.materials.new(name)
    result.diffuse_color = color
    result.roughness = roughness
    result.use_nodes = True
    principled = next(node for node in result.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = roughness
    MATERIALS[name] = result
    return result


def configure_object(
    name: str,
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
    material_name: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bevel: float = 0.0,
    post: Callable[[bpy.types.Object], None] | None = None,
):
    def configure(obj: bpy.types.Object):
        obj.name = name
        obj.location = location
        obj.rotation_mode = "XYZ"
        obj.rotation_euler = rotation
        obj.scale = scale
        if hasattr(obj.data, "materials"):
            obj.data.materials.clear()
            obj.data.materials.append(MATERIALS[material_name])
        if bevel > 0.0:
            modifier = obj.modifiers.new("Soft Low-Poly Edges", "BEVEL")
            modifier.width = bevel
            modifier.segments = 2
        if post:
            post(obj)

    return configure


def add_cube(name, location, scale, material_name, rotation=(0.0, 0.0, 0.0), bevel=0.0, post=None):
    ACTIONS.append(
        BuildAction(
            "mesh.primitive_cube_add",
            {"size": 2.0},
            configure_object(name, location, scale, material_name, rotation, bevel, post),
        )
    )


def add_cylinder(
    name,
    location,
    scale,
    material_name,
    rotation=(0.0, 0.0, 0.0),
    vertices=10,
    bevel=0.0,
):
    ACTIONS.append(
        BuildAction(
            "mesh.primitive_cylinder_add",
            {"vertices": vertices, "radius": 1.0, "depth": 2.0},
            configure_object(name, location, scale, material_name, rotation, bevel),
        )
    )


def add_cone(
    name,
    location,
    scale,
    material_name,
    rotation=(0.0, 0.0, 0.0),
    vertices=8,
    radius_top=0.0,
):
    ACTIONS.append(
        BuildAction(
            "mesh.primitive_cone_add",
            {
                "vertices": vertices,
                "radius1": 1.0,
                "radius2": radius_top,
                "depth": 2.0,
            },
            configure_object(name, location, scale, material_name, rotation),
        )
    )


def add_ico(name, location, scale, material_name, subdivisions=1):
    ACTIONS.append(
        BuildAction(
            "mesh.primitive_ico_sphere_add",
            {"subdivisions": subdivisions, "radius": 1.0},
            configure_object(name, location, scale, material_name),
        )
    )


def taper_wing(obj: bpy.types.Object):
    for vertex in obj.data.vertices:
        vertex.co.y *= 0.48 + 0.52 * (1.0 - abs(vertex.co.x))
    obj.data.update()


def taper_tail(obj: bpy.types.Object):
    for vertex in obj.data.vertices:
        if vertex.co.z > 0:
            vertex.co.x *= 0.45
            vertex.co.y *= 0.7
    obj.data.update()


def align_z_to(obj: bpy.types.Object, direction: Vector):
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = direction.normalized().to_track_quat("Z", "Y")


def make_sock_config(name, location, scale, material_name, direction):
    base = configure_object(name, location, scale, material_name)

    def configure(obj):
        base(obj)
        align_z_to(obj, Vector(direction))

    return configure


def setup_scene():
    bpy.context.preferences.view.show_splash = False
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)

    for item in list(bpy.data.materials):
        bpy.data.materials.remove(item)

    material("Sand", (0.68, 0.29, 0.17, 1.0))
    material("Sand Top", (0.92, 0.58, 0.36, 1.0))
    material("Runway", (0.055, 0.035, 0.10, 1.0), 0.84)
    material("Yellow", (1.0, 0.76, 0.02, 1.0), 0.58)
    material("White", (0.93, 0.92, 1.0, 1.0), 0.5)
    material("Lavender", (0.43, 0.18, 0.78, 1.0), 0.54)
    material("Purple", (0.22, 0.045, 0.50, 1.0), 0.5)
    material("Blue", (0.06, 0.08, 0.80, 1.0), 0.46)
    material("Navy", (0.025, 0.018, 0.12, 1.0), 0.42)
    material("Pink", (0.92, 0.12, 0.43, 1.0), 0.55)
    material("Red", (0.55, 0.025, 0.13, 1.0), 0.62)
    material("Tire", (0.018, 0.012, 0.035, 1.0), 0.92)
    material("Metal", (0.25, 0.11, 0.32, 1.0), 0.58)
    material("Rock A", (0.53, 0.29, 0.61, 1.0), 0.9)
    material("Rock B", (0.73, 0.47, 0.70, 1.0), 0.9)
    material("Grass", (0.47, 0.42, 0.20, 1.0), 0.92)

    world = bpy.context.scene.world or bpy.data.worlds.new("Airport World")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = next(node for node in world.node_tree.nodes if node.type == "BACKGROUND")
    background.inputs["Color"].default_value = (0.78, 0.43, 0.25, 1.0)
    background.inputs["Strength"].default_value = 0.35

    camera_data = bpy.data.cameras.new("Airport Camera Data")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 24.0
    camera = bpy.data.objects.new("Airport Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (16.5, -20.0, 15.0)
    camera.rotation_euler = (
        (Vector((0.0, 0.0, 1.8)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Key Light Data", "AREA")
    key_data.energy = 1500.0
    key_data.shape = "DISK"
    key_data.size = 8.0
    key_data.color = (1.0, 0.68, 0.48)
    key = bpy.data.objects.new("Key Light", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = (-5.0, -8.0, 18.0)
    key.rotation_euler = (
        (Vector((0.0, 0.0, 0.0)) - key.location).to_track_quat("-Z", "Y").to_euler()
    )

    fill_data = bpy.data.lights.new("Fill Light Data", "AREA")
    fill_data.energy = 900.0
    fill_data.size = 10.0
    fill_data.color = (0.45, 0.50, 1.0)
    fill = bpy.data.objects.new("Fill Light", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (9.0, 6.0, 10.0)
    fill.rotation_euler = (
        (Vector((0.0, 0.0, 1.0)) - fill.location).to_track_quat("-Z", "Y").to_euler()
    )

    scene = bpy.context.scene
    engine_items = scene.render.bl_rna.properties["engine"].enum_items.keys()
    scene.render.engine = (
        "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    )
    scene.render.resolution_x = 720
    scene.render.resolution_y = 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_percentage = 100
    scene.view_settings.look = "AgX - Medium High Contrast"


def define_actions():
    add_cube("Terrain Base", (0, 0, -0.75), (9.2, 7.2, 0.75), "Sand", bevel=0.16)
    add_cube("Terrain Top", (0, 0, 0.02), (9.0, 7.0, 0.12), "Sand Top", bevel=0.12)
    add_cube("Runway", (0, -0.2, 0.2), (3.15, 7.0, 0.13), "Runway", bevel=0.16)
    for index, y in enumerate((-5.6, -3.4, -1.2, 1.0, 3.2, 5.4), start=1):
        add_cube(f"Runway Dash {index}", (0, y, 0.36), (0.18, 0.72, 0.035), "Yellow", bevel=0.03)
    add_cube("Runway Edge Left", (-2.78, -0.2, 0.35), (0.07, 6.65, 0.025), "Yellow")
    add_cube("Runway Edge Right", (2.78, -0.2, 0.35), (0.07, 6.65, 0.025), "Yellow")

    add_cylinder(
        "Fuselage", (0, -0.15, 2.0), (0.82, 0.82, 3.2), "Lavender", (math.pi / 2, 0, 0), 10, 0.08
    )
    add_cone(
        "Nose Cowling", (0, -3.55, 2.0), (0.86, 0.86, 0.75), "Purple", (math.pi / 2, 0, 0), 10, 0.45
    )
    add_ico("Propeller Hub", (0, -4.23, 2.0), (0.48, 0.38, 0.48), "Blue", 2)
    add_cube("Cockpit", (0, -0.95, 2.72), (0.69, 1.18, 0.48), "Navy", (0.08, 0, 0), 0.18)
    add_cube(
        "Main Wing",
        (0, -0.05, 2.45),
        (5.35, 1.02, 0.13),
        "White",
        (0.02, 0, -0.035),
        0.08,
        taper_wing,
    )
    add_cube(
        "Wing Accent", (0, 0.25, 2.34), (4.3, 0.16, 0.055), "Lavender", (0.02, 0, -0.035), 0.03
    )
    add_cube(
        "Tail Plane", (0, 2.9, 2.45), (2.3, 0.72, 0.11), "Blue", (0, 0, 0.03), 0.06, taper_wing
    )
    add_cube(
        "Vertical Tail", (0, 2.82, 3.5), (0.14, 0.75, 1.42), "Blue", (-0.18, 0, 0), 0.06, taper_tail
    )
    add_cube(
        "Tail Fin Accent",
        (0, 2.94, 3.66),
        (0.18, 0.45, 0.92),
        "Purple",
        (-0.18, 0, 0),
        0.04,
        taper_tail,
    )

    for side in (-1, 1):
        add_cylinder(
            f"Main Gear Strut {'L' if side < 0 else 'R'}",
            (0.82 * side, -1.0, 0.95),
            (0.09, 0.09, 0.78),
            "Metal",
            (0, 0.18 * side, 0),
            8,
        )
        add_cylinder(
            f"Main Wheel {'L' if side < 0 else 'R'}",
            (1.05 * side, -1.05, 0.43),
            (0.42, 0.18, 0.42),
            "Tire",
            (0, math.pi / 2, 0),
            12,
            0.04,
        )
        add_cylinder(
            f"Wheel Hub {'L' if side < 0 else 'R'}",
            (1.05 * side, -1.05, 0.43),
            (0.17, 0.195, 0.17),
            "Pink",
            (0, math.pi / 2, 0),
            12,
        )
    add_cylinder("Tail Wheel Strut", (0, 2.55, 1.05), (0.07, 0.07, 0.7), "Metal", vertices=8)
    add_cylinder("Tail Wheel", (0, 2.63, 0.53), (0.26, 0.13, 0.26), "Tire", (math.pi / 2, 0, 0), 10)

    for index, angle in enumerate((45, 135, 225, 315), start=1):
        radians = math.radians(angle)
        add_cube(
            f"Propeller Blade {index}",
            (math.sin(radians) * 0.72, -4.37, 2.0 + math.cos(radians) * 0.72),
            (0.14, 0.08, 1.0),
            "White",
            (0, radians, 0),
            0.06,
        )
        add_cube(
            f"Propeller Tip {index}",
            (math.sin(radians) * 1.48, -4.38, 2.0 + math.cos(radians) * 1.48),
            (0.16, 0.09, 0.28),
            "Lavender",
            (0, radians, 0),
            0.04,
        )

    add_cylinder(
        "Windsock Base", (-6.4, 1.9, 0.75), (0.45, 0.45, 0.72), "Lavender", vertices=10, bevel=0.08
    )
    add_cylinder("Windsock Pole", (-6.4, 1.9, 3.25), (0.09, 0.09, 2.35), "Metal", vertices=10)
    direction = Vector((1.0, 0.06, 0.22))
    sock_origin = Vector((-6.05, 1.9, 5.48))
    for index in range(5):
        radius = 0.48 - index * 0.065
        location = sock_origin + direction.normalized() * (index * 0.58)
        material_name = "Pink" if index % 2 == 0 else "White"
        ACTIONS.append(
            BuildAction(
                "mesh.primitive_cone_add",
                {"vertices": 10, "radius1": 1.0, "radius2": 0.78, "depth": 2.0},
                make_sock_config(
                    f"Windsock Segment {index + 1}",
                    tuple(location),
                    (radius, radius, 0.36),
                    material_name,
                    direction,
                ),
            )
        )

    rocks = (
        (-6.5, -3.8, 0.55, 1.1, 0.8, 0.65),
        (-7.7, -2.9, 0.42, 0.7, 0.55, 0.5),
        (-5.6, -4.8, 0.38, 0.6, 0.5, 0.4),
        (5.9, -4.8, 0.48, 0.9, 0.7, 0.55),
        (7.2, -3.5, 0.35, 0.65, 0.5, 0.4),
        (6.9, 2.7, 0.5, 0.95, 0.65, 0.55),
        (5.4, 4.4, 0.42, 0.75, 0.58, 0.48),
        (-6.4, 4.7, 0.48, 0.85, 0.65, 0.55),
        (-7.7, 4.0, 0.36, 0.6, 0.45, 0.4),
        (7.5, 5.1, 0.32, 0.55, 0.42, 0.36),
    )
    for index, (x, y, z, sx, sy, sz) in enumerate(rocks, start=1):
        add_ico(
            f"Rock {index}",
            (x, y, z),
            (sx, sy, sz),
            "Rock A" if index % 2 else "Rock B",
            1,
        )

    shrubs = (
        (-7.3, -1.6),
        (-5.5, -3.2),
        (-7.0, 3.1),
        (-5.7, 4.0),
        (5.4, -3.9),
        (7.5, -1.8),
        (6.2, 3.4),
        (7.8, 4.0),
    )
    for index, (x, y) in enumerate(shrubs, start=1):
        add_cone(f"Shrub {index}", (x, y, 0.55), (0.26, 0.26, 0.68), "Grass", vertices=6)

    clouds = (
        (-3.0, 5.5, 6.3, 1.5),
        (-0.9, 5.7, 6.45, 1.2),
        (2.8, 5.9, 6.1, 1.6),
        (5.0, 5.7, 6.25, 1.1),
    )
    for index, (x, y, z, scale) in enumerate(clouds, start=1):
        add_ico(f"Cloud {index}", (x, y, z), (scale, 0.65 * scale, 0.42 * scale), "White", 1)


def object_pointer_set() -> set[int]:
    return {obj.as_pointer() for obj in bpy.context.scene.objects}


def send_event(event_type: str, value: str, x: int, y: int):
    bpy.context.window.event_simulate(type=event_type, value=value, x=x, y=y)


def viewport_center() -> tuple[int, int]:
    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    return region.x + region.width // 2, region.y + region.height // 2


def install_action_keymap(action: BuildAction):
    global KEYMAP_ITEM, KEYMAP_OPERATOR
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["Screen"]
    KEYMAP_ITEM = keymap.keymap_items.new(action.operator, type="F24", value="PRESS")
    KEYMAP_OPERATOR = action.operator
    for key, value in action.properties.items():
        setattr(KEYMAP_ITEM.properties, key, value)


def remove_action_keymap():
    global KEYMAP_ITEM, KEYMAP_OPERATOR
    if KEYMAP_ITEM is None:
        return
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["Screen"]
    item = next(
        (
            candidate
            for candidate in reversed(list(keymap.keymap_items))
            if candidate.idname == KEYMAP_OPERATOR and candidate.type == "F24"
        ),
        None,
    )
    if item is not None:
        keymap.keymap_items.remove(item)
    KEYMAP_ITEM = None
    KEYMAP_OPERATOR = ""


def build_step():
    global BUILD_INDEX, BUILD_PHASE, OBJECTS_BEFORE
    x, y = viewport_center()
    if BUILD_PHASE == "warmup":
        send_event("ESC", "PRESS", x, y)
        send_event("ESC", "RELEASE", x, y)
        send_event("MOUSEMOVE", "NOTHING", x, y)
        BUILD_PHASE = "invoke"
        return 0.15

    if BUILD_INDEX >= len(ACTIONS):
        remove_action_keymap()
        bpy.app.timers.register(finalize_verification, first_interval=1.2)
        return None

    action = ACTIONS[BUILD_INDEX]
    if BUILD_PHASE == "invoke":
        OBJECTS_BEFORE = object_pointer_set()
        install_action_keymap(action)
        send_event("F24", "PRESS", x, y)
        send_event("F24", "RELEASE", x, y)
        BUILD_PHASE = "configure"
        return 0.05

    new_objects = [
        obj for obj in bpy.context.scene.objects if obj.as_pointer() not in OBJECTS_BEFORE
    ]
    if not new_objects:
        return 0.03
    remove_action_keymap()
    runtime.poll(bpy.context)
    obj = new_objects[-1]
    action.configure(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    runtime.poll(bpy.context)
    runtime.commit_pending_state()
    BUILD_INDEX += 1
    BUILD_PHASE = "invoke"
    if BUILD_INDEX % 10 == 0:
        print(f"CHRONICLE_AIRPORT_PROGRESS {BUILD_INDEX}/{len(ACTIONS)}")
    return 0.035


def scene_descriptor() -> dict[str, Any]:
    objects: dict[str, Any] = {}
    for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name):
        if obj.type not in {"MESH", "CAMERA", "LIGHT"}:
            continue
        descriptor: dict[str, Any] = {
            "type": obj.type,
            "matrix_world": [[round(float(value), 6) for value in row] for row in obj.matrix_world],
        }
        if obj.type == "MESH":
            descriptor["mesh_digest"] = state.current_mesh_digest(obj)
            descriptor["materials"] = [
                slot.material.name if slot.material else None for slot in obj.material_slots
            ]
            descriptor["modifiers"] = [(modifier.name, modifier.type) for modifier in obj.modifiers]
        elif obj.type == "CAMERA":
            descriptor["camera"] = (obj.data.type, round(obj.data.ortho_scale, 6))
        elif obj.type == "LIGHT":
            descriptor["light"] = (obj.data.type, round(obj.data.energy, 6))
        objects[obj.name] = descriptor
    return {
        "objects": objects,
        "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
        "render": (
            bpy.context.scene.render.engine,
            bpy.context.scene.render.resolution_x,
            bpy.context.scene.render.resolution_y,
        ),
    }


def render_pixels(path: Path) -> array:
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    render = bpy.data.images.load(str(path), check_existing=False)
    pixels = array("f", [0.0]) * (render.size[0] * render.size[1] * 4)
    render.pixels.foreach_get(pixels)
    bpy.data.images.remove(render)
    return pixels


def compare_pixels(first: array, second: array) -> dict[str, float]:
    if len(first) != len(second):
        raise AssertionError(f"Render sizes differ: {len(first)} != {len(second)}")
    total = 0.0
    maximum = 0.0
    different = 0
    for left, right in zip(first, second, strict=True):
        difference = abs(left - right)
        total += difference
        maximum = max(maximum, difference)
        if difference > 1e-6:
            different += 1
    return {
        "mean_absolute_error": total / len(first),
        "maximum_absolute_error": maximum,
        "different_channel_fraction": different / len(first),
    }


def finalize_verification():
    try:
        runtime.poll(bpy.context)
        session = runtime.stop(bpy.context)
        storage.export_session(str(OUTPUT / "airport_scene.chronicle.json"), session)
        original_descriptor = scene_descriptor()
        original_digest = stable_digest(original_descriptor)
        original_pixels = render_pixels(OUTPUT / "airport_original.png")
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "airport_original.blend"))

        operator_events = [event for event in session["events"] if event.get("type") == "operator"]
        replay_result = playback.play(
            bpy.context,
            session,
            restore_baseline=True,
            use_checkpoints=True,
        )
        replay_descriptor = scene_descriptor()
        replay_digest = stable_digest(replay_descriptor)
        (OUTPUT / "original_descriptor.json").write_text(
            json.dumps(original_descriptor, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (OUTPUT / "replay_descriptor.json").write_text(
            json.dumps(replay_descriptor, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        replay_pixels = render_pixels(OUTPUT / "airport_replay.png")
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "airport_replay.blend"))

        pixel_comparison = compare_pixels(original_pixels, replay_pixels)
        report = {
            "blender_version": bpy.app.version_string,
            "actions_requested": len(ACTIONS),
            "operator_events_recorded": len(operator_events),
            "recording_events": len(session["events"]),
            "original_object_count": len(original_descriptor["objects"]),
            "replay_object_count": len(replay_descriptor["objects"]),
            "original_scene_digest": original_digest,
            "replay_scene_digest": replay_digest,
            "scene_structure_exact": original_descriptor == replay_descriptor,
            "replay_result": replay_result,
            "pixel_comparison": pixel_comparison,
        }
        (OUTPUT / "verification_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print("CHRONICLE_AIRPORT_REPORT", json.dumps(report, sort_keys=True))
        assert len(operator_events) == len(ACTIONS), (
            len(operator_events),
            len(ACTIONS),
        )
        assert original_descriptor == replay_descriptor
        assert pixel_comparison["mean_absolute_error"] < 1e-6
        assert not replay_result["warnings"], replay_result["warnings"]
        print("CHRONICLE_AIRPORT_VERIFICATION_OK")
    finally:
        addon.unregister()
        bpy.ops.wm.quit_blender()
    return None


setup_scene()
define_actions()
bpy.context.scene.chronicle_settings.recording_name = "Low-Poly Airport Diorama"
bpy.context.scene.chronicle_settings.max_checkpoint_vertices = 500_000
runtime.start(bpy.context)
bpy.app.timers.register(build_step, first_interval=0.25)
