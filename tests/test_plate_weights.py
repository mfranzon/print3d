"""Metadata weight belongs to a plate index, not to the whole archive."""
import zipfile
from xml.etree import ElementTree as ET

import pytest

from print3d.printability import analyze_slice
from print3d.project import inspect_project, slice_info_weight_g


def sliced_project(tmp_path, plates, *, gcode_weights=None, slice_info=True):
    path = tmp_path / "plates.gcode.3mf"
    root = ET.Element("config")
    with zipfile.ZipFile(path, "w") as archive:
        for index, weight in plates:
            plate = ET.SubElement(root, "plate")
            ET.SubElement(plate, "metadata", key="index", value=str(index))
            if weight is not None:
                ET.SubElement(plate, "metadata", key="weight", value=str(weight))
            header = ""
            if gcode_weights and index in gcode_weights:
                header = f"; total filament weight [g]: {gcode_weights[index]}\n"
            archive.writestr(f"Metadata/plate_{index}.gcode", header + "M83\nG1 Z0.2\nG1 X5 E1\n")
        if slice_info:
            archive.writestr("Metadata/slice_info.config", ET.tostring(root))
    return path


@pytest.mark.parametrize("plates", [[(1, 2.0), (2, 8.0)], [(7, 8.0), (2, 2.0)]])
def test_inspect_uses_matching_plate_index_not_xml_order(tmp_path, plates):
    path = sliced_project(tmp_path, plates)
    result = inspect_project(path)
    assert {plate["index"]: plate["filament_g"] for plate in result["plates"]} == dict(plates)
    for index, weight in plates:
        assert slice_info_weight_g(path, index) == weight
        assert analyze_slice(path, index)["filament_g"] == weight


@pytest.mark.parametrize("plates", [[(1, None), (2, 8.0)], [(1, 2.0), (2, None)], [(1, 0.0), (2, 8.0)]])
def test_missing_and_zero_weight_never_borrow_another_plates_weight(tmp_path, plates):
    path = sliced_project(tmp_path, plates)
    assert [p["filament_g"] for p in inspect_project(path)["plates"]] == [w for _, w in plates]


@pytest.mark.parametrize("weight", [0.0, 3.5])
def test_gcode_weight_takes_precedence_over_metadata(tmp_path, weight):
    path = sliced_project(tmp_path, [(1, 99.0)], gcode_weights={1: weight})
    assert inspect_project(path)["plates"][0]["filament_g"] == weight


def test_absent_slice_info_leaves_weight_unknown(tmp_path):
    path = sliced_project(tmp_path, [(1, 2.0)], slice_info=False)
    assert inspect_project(path)["plates"][0]["filament_g"] is None
