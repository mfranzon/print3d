from pathlib import Path

from print3d.flatten import build_name_index, flatten_by_name, normalize_for_cli


def test_inherits_and_include(tmp_path: Path):
    vendor = tmp_path / "BBL" / "machine"
    vendor.mkdir(parents=True)
    (vendor / "base.json").write_text(
        '{"type":"machine","name":"base","from":"system","a":1,"b":1}\n',
        encoding="utf-8",
    )
    (vendor / "inc.json").write_text(
        '{"name":"inc","machine_start_gcode":"G28"}\n',
        encoding="utf-8",
    )
    (vendor / "leaf.json").write_text(
        '{"type":"machine","name":"leaf","inherits":"base","include":["inc"],"b":2}\n',
        encoding="utf-8",
    )
    index = build_name_index(tmp_path)
    flat = flatten_by_name("leaf", index)
    assert flat["a"] == 1
    assert flat["b"] == 2
    assert flat["machine_start_gcode"] == "G28"
    normalize_for_cli(flat, "machine", "leaf")
    assert flat["from"] == "User"
    assert flat["inherits"] == "leaf"
    assert flat["printer_settings_id"] == "leaf"
    assert "include" not in flat
