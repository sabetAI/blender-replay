"""Launch Blender's UI and verify capture of a simulated user transform."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "blender_chronicle_ui_test"
spec = importlib.util.spec_from_file_location(
    PACKAGE_NAME,
    ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)],
)
addon = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE_NAME] = addon
spec.loader.exec_module(addon)
addon.register()

from blender_chronicle_ui_test import playback, runtime  # noqa: E402

runtime.start(bpy.context)


def send_key(key: str, x: int, y: int):
    window = bpy.context.window
    unicode = {"G": "g", "X": "x", "ONE": "1"}.get(key, "")
    window.event_simulate(type=key, value="PRESS", unicode=unicode, x=x, y=y)
    window.event_simulate(type=key, value="RELEASE", x=x, y=y)


keys = iter(("ESC", "MOUSEMOVE", "G", "X", "ONE", "RET"))


def simulate_transform():
    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    x = region.x + region.width // 2
    y = region.y + region.height // 2
    try:
        key = next(keys)
        if key == "MOUSEMOVE":
            bpy.context.window.event_simulate(type=key, value="NOTHING", x=x, y=y)
        else:
            send_key(key, x, y)
    except StopIteration:
        return None
    return 0.1


def verify_capture():
    try:
        runtime.poll(bpy.context)
        session = runtime.stop(bpy.context)
        identifiers = [
            event["idname"]
            for event in session["events"]
            if event.get("type") == "operator"
        ]
        assert "TRANSFORM_OT_translate" in identifiers, identifiers
        assert abs(bpy.context.scene.objects["Cube"].location.x - 1.0) < 1e-6
        bpy.context.scene.objects["Cube"].location.x = 9.0
        replay_result = playback.play(
            bpy.context,
            session,
            restore_baseline=True,
            use_checkpoints=True,
        )
        assert replay_result["warnings"] == [], replay_result["warnings"]
        assert replay_result["operators"] == 1, replay_result
        assert abs(bpy.context.scene.objects["Cube"].location.x - 1.0) < 1e-6
        print("CHRONICLE_UI_CAPTURE_TEST_OK")
    finally:
        addon.unregister()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(simulate_transform, first_interval=0.2)
bpy.app.timers.register(verify_capture, first_interval=3.0)
