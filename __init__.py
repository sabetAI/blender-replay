# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Replay action recorder extension."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from . import operators, runtime, ui

bl_info = {
    "name": "Blender Replay",
    "author": "sabetAI",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Replay",
    "description": "Record operators with selection and geometry checkpoints",
    "category": "System",
}


class CHRONICLE_PG_settings(PropertyGroup):
    recording_name: StringProperty(
        name="Recording Name",
        default="Modeling Action",
        maxlen=128,
    )
    geometry_checkpoints: BoolProperty(
        name="Geometry Checkpoints",
        description="Store mesh topology after changes and repair divergent replay",
        default=True,
    )
    max_checkpoint_vertices: IntProperty(
        name="Vertex Limit",
        description="Skip full geometry checkpoints above this many vertices",
        default=250_000,
        min=1_000,
        max=10_000_000,
    )
    use_checkpoints_on_replay: BoolProperty(
        name="Repair Divergent Geometry",
        description="Apply a recorded mesh checkpoint when operator replay differs",
        default=True,
    )


CLASSES = (
    CHRONICLE_PG_settings,
    *operators.CLASSES,
    *ui.CLASSES,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.chronicle_settings = PointerProperty(type=CHRONICLE_PG_settings)
    bpy.types.Scene.chronicle_session_text = StringProperty(options={"HIDDEN"})
    bpy.types.WindowManager.chronicle_is_recording = BoolProperty(
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    runtime.register()


def unregister():
    runtime.unregister()
    del bpy.types.WindowManager.chronicle_is_recording
    del bpy.types.Scene.chronicle_session_text
    del bpy.types.Scene.chronicle_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
