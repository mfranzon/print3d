"""Which printer print3d slices for, and what follows from that choice."""

import json
import re
from pathlib import Path

import pytest

from print3d import printers
from print3d.cli import main
from print3d.dfm import check_mesh
from print3d.filaments import colour_advice
from print3d.flatten import FlattenError, flatten_printer
from print3d.handoff import job_printer
from print3d.paths import default_profiles_dir
from print3d.pipeline import run_job

BUILT_IN = ("x2d-0.4", "p2s-0.4")


@pytest.fixture(autouse=True)
def no_printer_in_the_environment(monkeypatch):
    monkeypatch.delenv(printers.PRINTER_ENV, raising=False)


def _box_stl(path: Path, x: float, y: float, z: float) -> Path:
    corners = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]
    lines = ["solid box"]
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            lines += ["  facet normal 0 0 0", "    outer loop"]
            lines += [f"      vertex {corners[i][0]:g} {corners[i][1]:g} {corners[i][2]:g}" for i in tri]
            lines += ["    endloop", "  endfacet"]
    lines.append("endsolid box")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def test_every_known_printer_has_its_presets_pinned():
    assert set(printers.available()) == set(printers.PRESETS) >= set(BUILT_IN)
    for key in BUILT_IN:
        for name in ("machine.json", "process.json", "filament.json", "provenance.json"):
            assert (printers.PROFILES / key / name).is_file()


def test_the_presets_say_what_each_printer_is():
    x2d, p2s = printers.printer_info("x2d-0.4"), printers.printer_info("p2s-0.4")
    assert (x2d.name, x2d.nozzles, x2d.bed_mm) == ("Bambu Lab X2D", 2, (256.0, 256.0, 261.0))
    assert (p2s.name, p2s.nozzles, p2s.bed_mm) == ("Bambu Lab P2S", 1, (256.0, 256.0, 256.0))
    for printer in (x2d, p2s):
        assert (printer.nozzle_mm, printer.layer_mm, printer.bed_type) == (0.4, 0.2, "Textured PEI Plate")
        # Every overhang note relies on the slicer adding no supports.
        assert printer.supports is False
        assert printer.filament.startswith("Bambu PLA Basic @BBL")


def test_the_p2s_presets_are_flat_and_belong_together():
    folder = printers.PROFILES / "p2s-0.4"
    machine, process, filament = (
        json.loads((folder / f"{kind}.json").read_text()) for kind in ("machine", "process", "filament")
    )
    presets = printers.PRESETS["p2s-0.4"]
    assert machine["printer_settings_id"] == presets.machine
    assert process["print_settings_id"] == presets.process
    assert filament["filament_settings_id"] == [presets.filament]
    for flat in (machine, process, filament):
        # The Studio CLI does not walk the inherits chain: every preset must be complete.
        assert flat["from"] == "User"
        assert "include" not in flat
    assert presets.machine in process["compatible_printers"]
    assert presets.machine in filament["compatible_printers"]
    provenance = json.loads((folder / "provenance.json").read_text())
    assert (provenance["machine"], provenance["process"], provenance["filaments"]) == (
        presets.machine, presets.process, [presets.filament]
    )


def test_the_print_sheet_line_reads_the_presets():
    # The X2D line is word for word what the sheet hard-coded before.
    assert printers.printer_info("x2d-0.4").describe() == (
        "Bambu Lab X2D, 0.4 mm nozzle, 0.20 mm layers, Bambu PLA Basic, textured PEI, supports off"
    )
    assert printers.printer_info("p2s-0.4").describe() == (
        "Bambu Lab P2S, 0.4 mm nozzle, 0.20 mm layers, Bambu PLA Basic, textured PEI, supports off"
    )


def test_the_x2d_is_the_default():
    assert printers.printer_choice() == {"printer": "x2d-0.4", "source": "default"}
    assert default_profiles_dir() == printers.PROFILES / "x2d-0.4"


def test_a_saved_choice_sticks_and_the_environment_overrides_it(own_printer_choice, monkeypatch):
    printers.choose_printer("p2s-0.4")
    assert json.loads(own_printer_choice.read_text())["printer"] == "p2s-0.4"
    assert printers.printer_choice() == {"printer": "p2s-0.4", "source": str(own_printer_choice)}
    assert default_profiles_dir() == printers.PROFILES / "p2s-0.4"
    monkeypatch.setenv(printers.PRINTER_ENV, "x2d-0.4")
    assert printers.printer_choice() == {"printer": "x2d-0.4", "source": "$PRINT3D_PRINTER"}


@pytest.mark.parametrize("key", ["a1-0.4", "../x2d-0.4", "x2d-0.4/..", "X2D-0.4", "p2s"])
def test_an_unknown_printer_is_refused(key, own_printer_choice, monkeypatch):
    with pytest.raises(ValueError, match="p2s-0.4, x1c-0.4, x2d-0.4"):
        printers.choose_printer(key)
    assert not own_printer_choice.exists()
    monkeypatch.setenv(printers.PRINTER_ENV, key)
    with pytest.raises(ValueError, match="no presets"):
        default_profiles_dir()


def test_a_saved_printer_that_is_gone_fails_closed(own_printer_choice):
    """Falling back to another printer would slice for the wrong machine without a word."""
    own_printer_choice.parent.mkdir(parents=True)
    own_printer_choice.write_text('{"printer": "a1-0.4"}')
    with pytest.raises(ValueError, match=re.escape(str(own_printer_choice))):
        printers.active_printer()
    own_printer_choice.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        printers.active_printer()


def test_the_build_volume_check_follows_the_printer(tmp_path, monkeypatch):
    tall = _box_stl(tmp_path / "tall.stl", 20, 20, 258)
    monkeypatch.setenv(printers.PRINTER_ENV, "x2d-0.4")
    assert check_mesh(tall).ok
    monkeypatch.setenv(printers.PRINTER_ENV, "p2s-0.4")
    report = check_mesh(tall)
    assert not report.ok
    assert any("exceeds the Bambu Lab P2S build volume" in error for error in report.errors)


def test_colours_beyond_the_nozzles_get_a_note():
    x2d, p2s = printers.printer_info("x2d-0.4"), printers.printer_info("p2s-0.4")
    assert colour_advice(1, p2s) is None
    assert colour_advice(2, x2d) is None
    on_p2s, on_x2d = colour_advice(2, p2s), colour_advice(3, x2d)
    assert on_p2s is not None and on_x2d is not None
    assert on_p2s.severity == "note"
    assert on_p2s.message.startswith("two colours on the Bambu Lab P2S's one nozzle:")
    assert on_p2s.fix == "Needs an AMS to print unattended; without one, keep to one colour per plate."
    assert on_x2d.message.startswith("three colours on the Bambu Lab X2D's two nozzles:")


def test_a_job_records_the_printer_it_was_checked_for(tmp_path, cube_stl, monkeypatch):
    monkeypatch.setenv(printers.PRINTER_ENV, "p2s-0.4")
    report = run_job("a cube", mesh=cube_stl, execute=False, jobs_dir=tmp_path / "jobs")
    assert report["printer"]["key"] == "p2s-0.4"
    assert Path(report["slice_plan"]["request"]["machine"]) == printers.PROFILES / "p2s-0.4" / "machine.json"


def test_a_sheet_names_the_printer_its_job_was_sliced_for():
    p2s = printers.printer_info("p2s-0.4").as_dict()
    assert job_printer({"printer": p2s})["summary"].startswith("Bambu Lab P2S, ")
    # A report from before print3d knew several printers is an X2D job.
    assert job_printer({})["name"] == "Bambu Lab X2D"


def test_the_cli_shows_and_switches_the_printer(capsys, own_printer_choice):
    assert main(["printer"]) == 0
    assert json.loads(capsys.readouterr().out)["printer"] == "x2d-0.4"
    assert main(["printer", "p2s-0.4"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["printer"], shown["source"]) == ("p2s-0.4", str(own_printer_choice))
    assert main(["printer", "a1-0.4"]) == 1
    assert "no presets" in capsys.readouterr().err


def test_flatten_resolves_a_printers_presets(tmp_path):
    """The P2S inherits chain in miniature, as Studio ships it."""
    root = tmp_path / "profiles"

    def write(kind: str, data: dict) -> None:
        folder = root / "BBL" / kind
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{data['name']}.json").write_text(json.dumps(data))

    write("machine", {"type": "machine", "name": "fdm_common", "from": "system",
                      "nozzle_diameter": ["0.4"], "printable_height": "250"})
    write("machine", {"type": "machine", "name": "Bambu Lab P2S 0.4 nozzle", "inherits": "fdm_common",
                      "from": "system", "printer_model": "Bambu Lab P2S", "printable_height": "256"})
    write("process", {"type": "process", "name": "0.20mm Standard @BBL P2S", "from": "system",
                      "layer_height": "0.2", "enable_support": "0"})
    write("filament", {"type": "filament", "name": "Bambu PLA Basic @BBL P2S", "from": "system",
                       "filament_type": ["PLA"]})
    (root / "BBL.json").write_text('{"version": "02.08.00.06"}')

    result = flatten_printer("p2s-0.4", profiles_root=root, output_dir=tmp_path / "out")
    machine = json.loads(Path(result["machine_path"]).read_text())
    assert (machine["printable_height"], machine["nozzle_diameter"], machine["from"]) == ("256", ["0.4"], "User")
    process = json.loads(Path(result["process_path"]).read_text())
    assert process["compatible_printers"] == ["Bambu Lab P2S 0.4 nozzle"]
    assert process["curr_bed_type"] == "Textured PEI Plate"
    assert result["provenance"]["profiles_version"] == "02.08.00.06"


def test_flatten_refuses_a_printer_it_has_no_presets_for(tmp_path):
    with pytest.raises(FlattenError, match="p2s-0.4, x1c-0.4, x2d-0.4"):
        flatten_printer("a1-0.4", profiles_root=tmp_path, output_dir=tmp_path / "out")


def test_the_elegoo_printers_slice_in_elegooslicer():
    for key, name in (("elegoo-cc-0.4", "Elegoo Centauri Carbon"), ("elegoo-cc2-0.4", "Elegoo Centauri Carbon 2")):
        printer = printers.printer_info(key)
        assert (printer.name, printer.nozzles, printer.bed_mm) == (name, 1, (256.0, 256.0, 256.0))
        assert (printer.slicer, printer.slicer_name, printer.supports) == ("elegoo", "ElegooSlicer", False)
    for key in BUILT_IN:
        assert printers.printer_info(key).slicer_name == "Bambu Studio"


def test_a_job_slices_with_its_printers_slicer(tmp_path, cube_stl, monkeypatch):
    monkeypatch.setenv(printers.PRINTER_ENV, "elegoo-cc-0.4")
    report = run_job("a cube", mesh=cube_stl, execute=False, jobs_dir=tmp_path / "jobs")
    assert report["slice_plan"]["slicer"] == "ElegooSlicer"
    monkeypatch.setenv(printers.PRINTER_ENV, "x2d-0.4")
    report = run_job("a cube", mesh=cube_stl, execute=False, jobs_dir=tmp_path / "jobs")
    assert report["slice_plan"]["slicer"] == "Bambu Studio"


def test_the_slicer_is_never_swapped_for_the_other(tmp_path, monkeypatch):
    from print3d.studio import StudioError, find_slicer

    elegoo = tmp_path / "elegoo-slicer"
    elegoo.write_text("")
    elegoo.chmod(0o755)
    monkeypatch.setenv("ELEGOO_SLICER_BIN", str(elegoo))
    assert find_slicer("elegoo") == elegoo.resolve()
    monkeypatch.setenv("BAMBU_STUDIO_BIN", "")
    monkeypatch.setattr("print3d.studio.find_studio", lambda search_path=None: (_ for _ in ()).throw(StudioError("none")))
    with pytest.raises(StudioError):
        find_slicer("bambu")


def test_flatten_looks_only_in_the_requested_vendors_profiles(tmp_path, monkeypatch):
    from print3d.flatten import detect_profiles_root

    elegoo_root, bambu_root = tmp_path / "elegoo", tmp_path / "bambu"
    (elegoo_root / "Elegoo" / "machine").mkdir(parents=True)
    (bambu_root / "BBL" / "machine").mkdir(parents=True)
    monkeypatch.setenv("ELEGOO_PROFILES_ROOT", str(elegoo_root))
    monkeypatch.setenv("BAMBU_PROFILES_ROOT", str(bambu_root))
    assert detect_profiles_root(vendor="Elegoo") == elegoo_root
    assert detect_profiles_root(vendor="BBL") == bambu_root


def test_doctor_and_the_preflight_check_the_printers_slicer(tmp_path, cube_stl, monkeypatch):
    from print3d.doctor import discover_dependencies
    from print3d.pipeline import PipelineError
    from print3d.studio import StudioError

    monkeypatch.setenv(printers.PRINTER_ENV, "elegoo-cc-0.4")
    monkeypatch.setenv("ELEGOO_SLICER_BIN", "")
    monkeypatch.setattr("print3d.studio.find_elegoo", lambda search_path=None: (_ for _ in ()).throw(StudioError("ElegooSlicer CLI not found.")))
    studio = discover_dependencies()["studio"]
    assert (studio["ready"], studio["message"]) == (False, "ElegooSlicer CLI not found.")
    with pytest.raises((StudioError, PipelineError), match="ElegooSlicer"):
        run_job("a cube", mesh=cube_stl, execute=True, jobs_dir=tmp_path / "jobs")


def test_the_x1c_slices_with_one_nozzle_on_its_own_bed():
    # The X1C has one nozzle and Studio's 256 x 256 x 250 mm printable volume, so
    # the checks must not apply the X2D's two nozzles or its build height to it.
    printer = printers.printer_info("x1c-0.4")
    assert (printer.name, printer.nozzles, printer.slicer) == ("Bambu Lab X1 Carbon", 1, "bambu")
    assert printer.bed_mm == (256.0, 256.0, 250.0)
