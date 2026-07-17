# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Replay sidebar interface."""

from __future__ import annotations

from bpy.types import Panel

from . import runtime

EVENT_ICONS = {
    "operator": "PLAY",
    "context": "RESTRICT_SELECT_OFF",
    "checkpoint": "MESH_DATA",
}


class CHRONICLE_PT_recorder(Panel):
    bl_label = "Blender Replay"
    bl_idname = "CHRONICLE_PT_recorder"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Replay"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.chronicle_settings
        recording = runtime.is_recording()

        status = layout.box()
        row = status.row(align=True)
        row.label(
            text="Recording" if recording else "Ready",
            icon="REC" if recording else "CHECKMARK",
        )
        if recording:
            row.operator("chronicle.stop", text="Stop", icon="PAUSE")
            status.operator("chronicle.checkpoint", icon="FILE_TICK")
        else:
            status.prop(settings, "recording_name", text="Name")
            status.operator("chronicle.start", icon="REC")

        options = layout.box()
        options.label(text="Reliability", icon="SETTINGS")
        options.prop(settings, "geometry_checkpoints")
        column = options.column()
        column.enabled = settings.geometry_checkpoints
        column.prop(settings, "max_checkpoint_vertices")
        options.prop(settings, "use_checkpoints_on_replay")

        session = runtime.get_session(context)
        if session is None:
            info = layout.box()
            info.label(text="No recording loaded", icon="INFO")
            info.operator("chronicle.import", icon="IMPORT")
            return

        events = session.get("events", [])
        summary = layout.box()
        summary.label(text=session.get("name", "Recording"), icon="ACTION")
        summary.label(
            text=f"{len(events)} events • Blender {session.get('blender_version', '?')}"
        )
        if session.get("warnings"):
            summary.label(
                text=f"{len(session['warnings'])} capture warnings",
                icon="ERROR",
            )

        replay = layout.box()
        replay.label(text="Replay", icon="PLAY")
        operator = replay.operator(
            "chronicle.play",
            text="Restore Baseline + Replay",
            icon="RECOVER_LAST",
        )
        operator.restore_baseline = True
        operator = replay.operator("chronicle.play", text="Replay Here", icon="PLAY")
        operator.restore_baseline = False

        row = layout.row(align=True)
        row.operator("chronicle.export", text="Export", icon="EXPORT")
        row.operator("chronicle.import", text="Import", icon="IMPORT")
        layout.operator("chronicle.discard", icon="TRASH")

        if events:
            history = layout.box()
            history.label(text="Latest Events", icon="TIME")
            for event in events[-6:]:
                event_type = event.get("type", "event")
                if event_type == "operator":
                    label = event.get("label") or event.get("idname", "Operator")
                elif event_type == "checkpoint":
                    meshes = len(event.get("meshes", []))
                    label = f"Checkpoint ({meshes} mesh{'' if meshes == 1 else 'es'})"
                else:
                    label = "Selection / mode context"
                history.label(text=label[:52], icon=EVENT_ICONS.get(event_type, "DOT"))


CLASSES = (CHRONICLE_PT_recorder,)
