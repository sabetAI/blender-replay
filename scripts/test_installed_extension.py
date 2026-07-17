"""Smoke-test Blender Replay after installing its built extension package."""

import bpy

assert hasattr(bpy.context.scene, "chronicle_settings")
assert bpy.ops.chronicle.start.poll()
assert bpy.ops.chronicle.start() == {"FINISHED"}
assert bpy.context.window_manager.chronicle_is_recording
assert bpy.ops.chronicle.stop() == {"FINISHED"}
assert not bpy.context.window_manager.chronicle_is_recording
assert bpy.context.scene.chronicle_session_text
print("BLENDER_REPLAY_INSTALLED_EXTENSION_TEST_OK")
