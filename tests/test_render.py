"""Blender argv translation for the render paths, including WSL .exe binaries."""

# bearbeitet mit Hilfe von coding-bot-public mit nous/tencent/hy4-flash:free — 2026-11-24

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess

from print3d import blender, render

_EXE = Path("/mnt/c/Program Files/Blender Foundation/Blender 4.10/blender.exe")


def test_wsl_render_parts_translates_script_stl_and_output(tmp_path, monkeypatch):
    """A Windows blender.exe receives Windows paths; results are read via Linux paths."""
    monkeypatch.setattr(render, "find_blender", lambda: _EXE)
    monkeypatch.setattr(blender, "_wsl_windows_path", lambda path: f"W:{path}")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        captured["command"] = command
        Path(command[9].removeprefix("W:")).write_bytes(b"png")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    stl = tmp_path / "part.stl"
    stl.write_bytes(b"stl")
    out_png = tmp_path / "out.png"

    assert render.render_parts([{"stl": str(stl), "hex": "#ffffff"}], out_png) == out_png

    command = captured["command"]
    assert command[0] == str(_EXE)
    assert command[6].startswith("W:")  # render.py harness
    assert json.loads(command[8])[0]["stl"].startswith("W:")  # embedded STL path
    assert command[9].startswith("W:")  # out_png


def test_native_render_parts_keeps_linux_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "find_blender", lambda: tmp_path / "blender")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        captured["command"] = command
        Path(command[9]).write_bytes(b"png")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    stl = tmp_path / "part.stl"
    stl.write_bytes(b"stl")
    out_png = tmp_path / "out.png"
    render.render_parts([{"stl": str(stl), "hex": "#ffffff"}], out_png)

    command = captured["command"]
    assert command[6].endswith("render.py")
    assert json.loads(command[8])[0]["stl"] == str(stl)
    assert command[9] == str(out_png)


def test_workflow_media_turntable_translates_wsl_paths(tmp_path, monkeypatch):
    """The dev script's turntable uses the same argv rule as the library."""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "workflow_media.py"
    spec = importlib.util.spec_from_file_location("workflow_media_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "find_blender", lambda: _EXE)
    monkeypatch.setattr(blender, "_wsl_windows_path", lambda path: f"W:{path}")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        captured["command"] = command
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    mesh = tmp_path / "mesh.stl"
    mesh.write_bytes(b"stl")
    out = tmp_path / "frames"
    out.mkdir()

    assert module.turntable(mesh, out, 2) == []
    command = captured["command"]
    assert command[4].startswith("W:")  # turntable.py
    assert command[6].startswith("W:")  # mesh
    assert command[7].startswith("W:")  # output folder
