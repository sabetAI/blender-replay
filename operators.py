# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operators exposed by the Replay sidebar."""

from __future__ import annotations

import json

from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import playback, runtime, storage


class CHRONICLE_OT_start(Operator):
    bl_idname = "chronicle.start"
    bl_label = "Start Recording"
    bl_description = "Capture operators, selections, object state, and mesh checkpoints"

    @classmethod
    def poll(cls, context):
        return (
            not runtime.is_recording()
            and not runtime.can_resume(context)
            and context.scene is not None
        )

    def execute(self, context):
        try:
            runtime.start(context)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start Blender Replay: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Blender Replay recording started")
        return {"FINISHED"}


class CHRONICLE_OT_stop(Operator):
    bl_idname = "chronicle.stop"
    bl_label = "Stop Recording"
    bl_description = "Stop and save the current recording in this blend file"

    @classmethod
    def poll(cls, context):
        return runtime.is_recording() or runtime.can_resume(context)

    def execute(self, context):
        session = runtime.stop(context)
        count = len(session["events"]) if session else 0
        self.report({"INFO"}, f"Blender Replay saved {count} events")
        return {"FINISHED"}


class CHRONICLE_OT_pause(Operator):
    bl_idname = "chronicle.pause"
    bl_label = "Pause Recording"
    bl_description = "End this capture segment and ignore commands until recording resumes"

    @classmethod
    def poll(cls, context):
        return runtime.is_recording() and not runtime.is_paused(context)

    def execute(self, context):
        try:
            runtime.pause(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Blender Replay recording paused")
        return {"FINISHED"}


class CHRONICLE_OT_resume(Operator):
    bl_idname = "chronicle.resume"
    bl_label = "Resume Recording"
    bl_description = "Start a new capture segment from the current scene state"

    @classmethod
    def poll(cls, context):
        return runtime.can_resume(context)

    def execute(self, context):
        try:
            session = runtime.resume(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Recording segment {len(session['segments'])} started")
        return {"FINISHED"}


class CHRONICLE_OT_checkpoint(Operator):
    bl_idname = "chronicle.checkpoint"
    bl_label = "Capture State Now"
    bl_description = "Capture selection, transforms, modifiers, and active mesh geometry now"

    @classmethod
    def poll(cls, context):
        return runtime.is_recording() and not runtime.is_paused(context)

    def execute(self, context):
        runtime.poll(context)
        runtime.save(context)
        self.report({"INFO"}, "Current state captured")
        return {"FINISHED"}


class CHRONICLE_OT_play(Operator):
    bl_idname = "chronicle.play"
    bl_label = "Replay Recording"
    bl_description = "Replay the active Blender Replay recording"

    restore_baseline: BoolProperty(
        name="Restore Baseline",
        default=True,
        description="Restore the scene state from the start of recording before replaying",
    )

    @classmethod
    def poll(cls, context):
        return (
            not runtime.is_recording()
            and not runtime.can_resume(context)
            and runtime.get_session(context) is not None
        )

    def invoke(self, context, event):
        if self.restore_baseline:
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Restore recording baseline?",
                message="Objects created after recording began will be removed before replay.",
                confirm_text="Restore and Replay",
                icon="WARNING",
            )
        return self.execute(context)

    def execute(self, context):
        session = runtime.get_session(context)
        if session is None:
            self.report({"ERROR"}, "No Blender Replay recording is loaded")
            return {"CANCELLED"}
        result = playback.play(
            context,
            session,
            restore_baseline=self.restore_baseline,
            use_checkpoints=context.scene.chronicle_settings.use_checkpoints_on_replay,
        )
        warning_count = len(result["warnings"])
        if warning_count:
            for warning in result["warnings"][:5]:
                self.report({"WARNING"}, warning)
        self.report(
            {"INFO"},
            f"Replayed {result['operators']} operators; "
            f"{result['repairs']} geometry repairs; {warning_count} warnings",
        )
        return {"FINISHED"}


class CHRONICLE_OT_discard(Operator):
    bl_idname = "chronicle.discard"
    bl_label = "Discard Recording"
    bl_description = "Remove the loaded Blender Replay recording from this blend file"

    @classmethod
    def poll(cls, context):
        return runtime.get_session(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        runtime.discard(context)
        self.report({"INFO"}, "Blender Replay recording discarded")
        return {"FINISHED"}


class CHRONICLE_OT_export(Operator, ExportHelper):
    bl_idname = "chronicle.export"
    bl_label = "Export Recording"

    filename_ext = ".chronicle.json"
    filter_glob: StringProperty(default="*.chronicle.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return not runtime.is_recording() and runtime.get_session(context) is not None

    def execute(self, context):
        session = runtime.get_session(context)
        try:
            storage.export_session(self.filepath, session)
        except (OSError, ValueError, TypeError) as exc:
            self.report({"ERROR"}, f"Export failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(session['events'])} events")
        return {"FINISHED"}


class CHRONICLE_OT_import(Operator, ImportHelper):
    bl_idname = "chronicle.import"
    bl_label = "Import Recording"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json;*.chronicle.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return not runtime.is_recording()

    def execute(self, context):
        try:
            session = storage.import_session(self.filepath)
            runtime.set_session(context, session)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {len(session['events'])} events")
        return {"FINISHED"}


CLASSES = (
    CHRONICLE_OT_start,
    CHRONICLE_OT_stop,
    CHRONICLE_OT_pause,
    CHRONICLE_OT_resume,
    CHRONICLE_OT_checkpoint,
    CHRONICLE_OT_play,
    CHRONICLE_OT_discard,
    CHRONICLE_OT_export,
    CHRONICLE_OT_import,
)
