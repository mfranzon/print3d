"""Read application version metadata without launching the application."""

from __future__ import annotations

import ctypes
from pathlib import Path
import plistlib
import re
from xml.parsers.expat import ExpatError


def windows_file_version(binary: str) -> str | None:
    """Read the language-independent VS_FIXEDFILEINFO through Windows APIs."""
    # https://learn.microsoft.com/windows/win32/api/winver/nf-winver-verqueryvaluew
    from ctypes import wintypes

    api = ctypes.WinDLL("version.dll")
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    api.VerQueryValueW.restype = wintypes.BOOL
    path = str(Path(binary).resolve())
    size = api.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(path, 0, size, data):
        return None
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not api.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    # VS_FIXEDFILEINFO consists of thirteen DWORDs. The first four are the
    # signature, structure version, and high/low words of the file version.
    fixed_type = wintypes.DWORD * 13
    if not pointer.value or length.value < ctypes.sizeof(fixed_type):
        return None
    fixed = ctypes.cast(pointer, ctypes.POINTER(fixed_type)).contents
    if fixed[0] != 0xFEEF04BD:
        return None
    high, low = fixed[2], fixed[3]
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def macos_bundle_version(binary: str) -> str | None:
    """Read only the bundle containing the discovered executable."""
    path = Path(binary).resolve()
    if path.parent.name != "MacOS" or path.parent.parent.name != "Contents":
        return None
    try:
        with (path.parent.parent / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
    except (plistlib.InvalidFileException, ExpatError):
        return None
    if not isinstance(info, dict):
        return None
    # Bambu's CFBundleVersion is the build ID; ShortVersionString also includes
    # the application name in its upstream Info.plist template.
    for key in ("CFBundleVersion", "CFBundleShortVersionString"):
        value = info.get(key)
        if isinstance(value, str) and (match := re.search(r"\b\d+(?:\.\d+)+\b", value)):
            return match.group()
    return None
