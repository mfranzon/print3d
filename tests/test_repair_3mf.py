from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from print3d.repair_3mf import is_well_formed_model_settings, repair_bambu_3mf

GOLDEN = Path(__file__).parent / "fixtures" / "cube_20mm.gcode.3mf"


def test_repair_makes_model_settings_parse(tmp_path: Path):
    copy = tmp_path / "cube.gcode.3mf"
    copy.write_bytes(GOLDEN.read_bytes())
    assert not is_well_formed_model_settings(copy)
    repair_bambu_3mf(copy)
    assert is_well_formed_model_settings(copy)
    with ZipFile(copy) as archive:
        xml = archive.read("Metadata/model_settings.config")
        root = ET.fromstring(xml)
        assert root.find("object") is not None
        assert root.find("plate") is not None
        assert root.find("assemble/assemble_item") is not None
        mesh = archive.read("3D/Objects/object_1.model")
        assert b"<vertex " in mesh
        for info in archive.infolist():
            assert info.flag_bits & 0x8 == 0
