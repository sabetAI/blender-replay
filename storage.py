# SPDX-License-Identifier: GPL-3.0-or-later
"""Persist recordings in Blender text data-blocks and portable JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bpy

from .core import validate_session

TEXT_PREFIX = ".Blender Replay - "


def _safe_name(name: str) -> str:
    clean = "".join(character for character in name if character not in "\n\r\t")
    return clean.strip() or "Recording"


def write_session(context: bpy.types.Context, session: dict[str, Any]) -> bpy.types.Text:
    text_name = context.scene.chronicle_session_text
    text = bpy.data.texts.get(text_name) if text_name else None
    if text is None:
        text = bpy.data.texts.new(TEXT_PREFIX + _safe_name(session.get("name", "Recording")))
        context.scene.chronicle_session_text = text.name
    text.clear()
    text.write(json.dumps(session, indent=2, ensure_ascii=False, sort_keys=True))
    return text


def read_session(context: bpy.types.Context) -> dict[str, Any] | None:
    text = bpy.data.texts.get(context.scene.chronicle_session_text)
    if text is None:
        return None
    try:
        return validate_session(json.loads(text.as_string()))
    except (json.JSONDecodeError, ValueError):
        return None


def export_session(path: str, session: dict[str, Any]) -> None:
    validate_session(session)
    Path(path).write_text(
        json.dumps(session, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def import_session(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_session(value)
