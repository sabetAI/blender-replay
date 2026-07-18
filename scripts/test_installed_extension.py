"""Smoke-test Blender Replay after installing its built extension package."""

import json

import bpy

assert hasattr(bpy.context.scene, "chronicle_settings")
assert bpy.ops.chronicle.start.poll()
assert bpy.ops.chronicle.start() == {"FINISHED"}
assert bpy.context.window_manager.chronicle_is_recording
assert bpy.ops.chronicle.pause() == {"FINISHED"}
assert bpy.context.window_manager.chronicle_is_paused
assert bpy.ops.chronicle.resume() == {"FINISHED"}
assert not bpy.context.window_manager.chronicle_is_paused
assert bpy.ops.chronicle.stop() == {"FINISHED"}
assert not bpy.context.window_manager.chronicle_is_recording
assert bpy.context.scene.chronicle_session_text
text = bpy.data.texts[bpy.context.scene.chronicle_session_text]
session = json.loads(text.as_string())
assert len(session["segments"]) == 2
print("BLENDER_REPLAY_INSTALLED_EXTENSION_TEST_OK")
