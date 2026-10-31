"""On-demand dependency diagnostics. No jobs or agent settings are written."""

from __future__ import annotations

import platform
import re
import subprocess
import tempfile
from typing import Any

from print3d.app_version import macos_bundle_version, windows_file_version
from print3d.blender import BlenderError, find_blender
from print3d.studio import StudioError, find_studio

PROBE_TIMEOUT = 5
OUTPUT_LIMIT = 65536
ERROR_TAIL = 2000
SYSTEM = platform.system()


def discover_dependencies() -> dict[str, dict[str, Any]]:
    """Shared lookup for status and doctor; never start an application."""
    checks = {}
    for name, finder in (("blender", find_blender), ("studio", find_studio)):
        check: dict[str, Any] = {"path": None, "version": None, "ready": False}
        checks[name] = check
        try:
            binary = finder()
        except (BlenderError, StudioError) as exc:
            check.update(status="missing", message=str(exc))
            continue
        check["path"] = str(binary)
        check.update(status="available", ready=True, message="Executable found.")
    return checks


def _probe(binary: str, flag: str) -> subprocess.CompletedProcess:
    # Regular files do not wait for EOF from a helper inheriting stdout/stderr.
    # run() kills and waits for the direct child on timeout; it does not promise
    # to terminate independently spawned descendants of a GUI application.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(
            [binary, flag], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            timeout=PROBE_TIMEOUT, check=False,
        )
        stdout.seek(0)
        stderr.seek(0, 2)
        stderr.seek(max(0, stderr.tell() - OUTPUT_LIMIT))
        return subprocess.CompletedProcess(
            result.args, result.returncode,
            stdout.read(OUTPUT_LIMIT).decode("utf-8", errors="replace"),
            stderr.read(OUTPUT_LIMIT).decode("utf-8", errors="replace"),
        )


def dependency_status() -> dict[str, Any]:
    checks = discover_dependencies()
    for name, check in checks.items():
        if not check["ready"]:
            continue
        # Studio readiness is discovery-based. Its GUI startup/help behavior
        # must not prevent modelling; the version probe is informational only.
        required = name == "blender"
        if not required and SYSTEM in ("Windows", "Darwin"):
            try:
                reader = windows_file_version if SYSTEM == "Windows" else macos_bundle_version
                check["version"] = reader(check["path"])
                check["message"] = ("Executable found; version read from application metadata."
                                    if check["version"] else "Executable found; version metadata unavailable.")
            except (OSError, ValueError) as exc:
                check["message"] = f"Executable found; version metadata unavailable: {exc}"
            continue
        try:
            result = _probe(check["path"], "--version" if required else "--help")
        except (OSError, subprocess.TimeoutExpired) as exc:
            check.update(message=f"Version probe failed: {exc}")
            if required:
                check.update(status="unavailable", ready=False)
            continue
        version = re.search(
            r"(?:Blender|Bambu\s*Studio|BambuStudio)[ \t-]+(?:version[ \t]+)?v?(\d+(?:\.\d+)+)",
            result.stdout + "\n" + result.stderr, re.IGNORECASE,
        )
        check.update(
            version=version.group(1) if version else None,
            message="Dependency probe succeeded." if version else "Dependency probe succeeded; version unknown.",
        )
        if result.returncode != 0:
            check["message"] = f"Dependency probe exited {result.returncode}. {result.stderr[-ERROR_TAIL:].strip()}".rstrip()
            if required:
                check.update(status="unavailable", ready=False)
    return {"ready": all(check["ready"] for check in checks.values()), "dependencies": checks}
