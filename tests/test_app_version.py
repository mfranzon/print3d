import plistlib
import re
import sys

import pytest

from print3d.app_version import macos_bundle_version, windows_file_version


@pytest.mark.parametrize("metadata,expected", [
    ({"CFBundleVersion": "02.08.02.61", "CFBundleShortVersionString": "BambuStudio 02.08.02.61"}, "02.08.02.61"),
    ({"CFBundleShortVersionString": "BambuStudio 02.08.02.61"}, "02.08.02.61"),
    ({"CFBundleVersion": 42}, None),
    ({}, None),
    ([], None),
])
def test_bundle_metadata(tmp_path, metadata, expected):
    contents = tmp_path / "Bambu Studio.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
    assert macos_bundle_version(str(contents / "MacOS" / "BambuStudio")) == expected


def test_non_bundle_path_does_not_read_unrelated_metadata(tmp_path):
    (tmp_path / "Info.plist").write_bytes(plistlib.dumps({"CFBundleVersion": "1.2.3"}))
    assert macos_bundle_version(str(tmp_path / "studio")) is None


@pytest.mark.parametrize("content", [b"not a plist", b"<?xml version='1.0'?><plist><dict>"])
def test_malformed_bundle_metadata(tmp_path, content):
    contents = tmp_path / "BambuStudio.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(content)
    assert macos_bundle_version(str(contents / "MacOS" / "BambuStudio")) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version API")
def test_windows_native_version_api(tmp_path):
    # Exercise the real ABI using the Python executable, without requiring Studio.
    assert re.fullmatch(r"\d+(?:\.\d+){3}", windows_file_version(sys.executable))
    plain = tmp_path / "unversioned.exe"
    plain.write_text("No PE version resource")
    assert windows_file_version(str(plain)) is None
    assert windows_file_version(str(tmp_path / "absent.exe")) is None
