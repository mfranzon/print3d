"""Fix CLI-exported Bambu 3mf files so Studio File > Open can load them.

Studio CLI writes Metadata/model_settings.config with illegal XML
(value=""quoted""). The GUI treats a parse failure as "no geometry data"
even when 3D/Objects/object_1.model is present.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

OBJECT_RE = re.compile(r'<object id="(\d+)"')
NAME_RE = re.compile(r'<metadata key="name" value="([^"]*)"')
FACE_RE = re.compile(r'<metadata face_count="(\d+)"')
IDENT_RE = re.compile(r'<metadata key="identify_id" value="(\d+)"')
PART_OFFSET_RE = re.compile(r'<metadata key="source_offset_([xyz])" value="([^"]*)"')
ITEM_TRANSFORM_RE = re.compile(r'<item objectid="[^"]+"[^>]*transform="([^"]+)"')
GCODE_MEMBER_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")


class RepairError(RuntimeError):
    """Raised when a 3mf cannot be repaired."""


def _xml_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _rebuild_model_settings(files: dict[str, bytes]) -> bytes:
    raw = files.get("Metadata/model_settings.config", b"").decode("utf-8", "replace")
    object_id = OBJECT_RE.search(raw).group(1) if OBJECT_RE.search(raw) else "2"
    name_match = NAME_RE.search(raw)
    name = name_match.group(1) if name_match else "model"
    face = FACE_RE.search(raw).group(1) if FACE_RE.search(raw) else "0"
    identify = IDENT_RE.search(raw).group(1) if IDENT_RE.search(raw) else "1"
    offsets = {"x": "0", "y": "0", "z": "0"}
    for axis, value in PART_OFFSET_RE.findall(raw):
        offsets[axis] = value

    transform = "1 0 0 0 1 0 0 0 1 0 0 0"
    model_xml = files.get("3D/3dmodel.model", b"").decode("utf-8", "replace")
    item = ITEM_TRANSFORM_RE.search(model_xml)
    if item:
        transform = item.group(1)

    gcode_name = ""
    for member in files:
        if GCODE_MEMBER_RE.match(member):
            gcode_name = member
            break

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="{_xml_attr(object_id)}">
    <metadata key="name" value="{_xml_attr(name)}"/>
    <metadata key="extruder" value="1"/>
    <metadata face_count="{_xml_attr(face)}"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="{_xml_attr(name)}"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <metadata key="source_object_id" value="0"/>
      <metadata key="source_volume_id" value="0"/>
      <metadata key="source_offset_x" value="{_xml_attr(offsets['x'])}"/>
      <metadata key="source_offset_y" value="{_xml_attr(offsets['y'])}"/>
      <metadata key="source_offset_z" value="{_xml_attr(offsets['z'])}"/>
      <mesh_stat face_count="{_xml_attr(face)}" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="gcode_file" value="{_xml_attr(gcode_name)}"/>
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
    <metadata key="thumbnail_no_light_file" value="Metadata/plate_no_light_1.png"/>
    <metadata key="top_file" value="Metadata/top_1.png"/>
    <metadata key="pick_file" value="Metadata/pick_1.png"/>
    <model_instance>
      <metadata key="object_id" value="{_xml_attr(object_id)}"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="{_xml_attr(identify)}"/>
    </model_instance>
  </plate>
  <assemble>
   <assemble_item object_id="{_xml_attr(object_id)}" instance_id="0" transform="{_xml_attr(transform)}" offset="0 0 0" />
  </assemble>
</config>
"""
    ET.fromstring(xml)
    return xml.encode("utf-8")


def repair_bambu_3mf(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RepairError(f"3mf does not exist: {resolved}")
    with zipfile.ZipFile(resolved) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if "3D/Objects/object_1.model" not in files and "3D/3dmodel.model" not in files:
        raise RepairError(f"{resolved} has no 3D model members")
    files["Metadata/model_settings.config"] = _rebuild_model_settings(files)
    plate = files.get("Metadata/plate_1.png")
    small = files.get("Metadata/plate_1_small.png") or plate
    if plate:
        files.setdefault("Auxiliaries/.thumbnails/thumbnail_3mf.png", plate)
        files.setdefault("Auxiliaries/.thumbnails/thumbnail_middle.png", plate)
    if small:
        files.setdefault("Auxiliaries/.thumbnails/thumbnail_small.png", small)
    rels = files.get("_rels/.rels", b"").decode("utf-8", "replace")
    if "Auxiliaries/.thumbnails/thumbnail_3mf.png" in files and "Auxiliaries/.thumbnails" not in rels:
        files["_rels/.rels"] = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Auxiliaries/.thumbnails/thumbnail_3mf.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
 <Relationship Target="/Auxiliaries/.thumbnails/thumbnail_middle.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>
 <Relationship Target="/Auxiliaries/.thumbnails/thumbnail_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>
</Relationships>
""".encode("utf-8")

    tmp = resolved.with_name(resolved.name + ".rewriting")
    names = list(files)
    head = [name for name in ("[Content_Types].xml", "_rels/.rels") if name in files]
    rest = [name for name in names if name not in head]
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as out:
        for name in head + rest:
            info = zipfile.ZipInfo(filename=name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            out.writestr(info, files[name])
    tmp.replace(resolved)
    return resolved


def is_well_formed_model_settings(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        if "Metadata/model_settings.config" not in archive.namelist():
            return False
        try:
            ET.fromstring(archive.read("Metadata/model_settings.config"))
        except ET.ParseError:
            return False
    return True
