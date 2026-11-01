"""Standard Windows installation locations, without launching applications."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path


def program_files_dirs() -> Iterator[Path]:
    if sys.platform != "win32":
        return
    seen: set[Path] = set()
    # ProgramW6432 also finds native 64-bit installs from a 32-bit Python.
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable, "").strip()
        if value:
            path = Path(value)
            if path not in seen:
                seen.add(path)
                yield path


def blender_locations() -> Iterator[Path]:
    for root in program_files_dirs():
        # Compare numeric versions: Blender 4.10 must sort after Blender 4.9.
        candidates = root.glob("Blender Foundation/Blender */blender.exe")
        yield from sorted(
            candidates,
            key=lambda path: (tuple(int(n) for n in re.findall(r"\d+", path.parent.name)), str(path)),
            reverse=True,
        )


def studio_locations() -> Iterator[Path]:
    for root in program_files_dirs():
        yield root / "Bambu Studio" / "bambu-studio.exe"


def studio_profiles_locations() -> Iterator[Path]:
    # The Windows install keeps the profile tree beside the executable, where
    # the macOS bundle has it one level up in Contents/Resources.
    for root in program_files_dirs():
        yield root / "Bambu Studio" / "resources" / "profiles"
