import json

import anyio
import pytest
from mcp import Client

from print3d.mcp_server import GUIDES, server
from print3d.paths import skill_dir
from test_likeness import CARD_PX, L_OUTLINE, _l_part, _photo


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_JOBS", str(tmp_path / "jobs"))
    return tmp_path / "jobs"


def _call(*calls):
    async def run():
        async with Client(server) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return anyio.run(run)


def test_every_cli_step_is_a_tool():
    async def names():
        async with Client(server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert {"guide", "make", "grid", "measure_scale", "measure_dim", "measure_outline",
            "add_unknown", "set_listing", "show_spec", "likeness", "check",
            "handoff", "fit_coupon", "fits", "printer"} <= anyio.run(names)


def test_the_printer_tool_shows_and_switches(monkeypatch):
    monkeypatch.delenv("PRINT3D_PRINTER", raising=False)
    shown, chosen, refused = _call(("printer", {}), ("printer", {"choose": "p2s-0.4"}),
                                   ("printer", {"choose": "a1-0.4"}))
    assert json.loads(shown.content[0].text)["printer"] == "x2d-0.4"
    info = json.loads(chosen.content[0].text)["info"]
    assert (info["name"], info["nozzles"], info["bed_mm"]) == ("Bambu Lab P2S", 1, [256.0, 256.0, 256.0])
    assert refused.is_error
    assert "p2s-0.4" in refused.content[0].text


@pytest.mark.parametrize("topic", GUIDES)
def test_guide_serves_the_skill(topic):
    [result] = _call(("guide", {"topic": topic}))
    assert not result.is_error
    assert result.content[0].text == (skill_dir() / GUIDES[topic]).read_text(encoding="utf-8")


def test_grid_comes_back_as_an_image(tmp_path, jobs):
    photo = _photo(tmp_path / "photo.png")
    [result] = _call(("grid", {"photo": str(photo), "crop": [280, 480, 520, 670]}))
    assert [c.type for c in result.content] == ["text", "image"]
    assert json.loads(result.content[0].text)["crop"] == [280, 480, 520, 670]


def test_photo_to_likeness_over_mcp(tmp_path, jobs):
    photo = _photo(tmp_path / "photo.png")
    mesh = _l_part(tmp_path / "l.stl")
    *_, spec, likeness = _call(
        ("measure_scale", {"slug": "l", "view": "front", "photo": str(photo), "ref": "card",
                           "points": [[100, 100], [100 + CARD_PX, 100]]}),
        ("measure_dim", {"slug": "l", "name": "width", "view": "front", "points": [[300, 650], [500, 650]]}),
        ("measure_dim", {"slug": "l", "name": "depth", "mm": 15, "source": "assumed", "why": "not shown"}),
        ("measure_outline", {"slug": "l", "view": "front", "points": L_OUTLINE}),
        ("show_spec", {"slug": "l"}),
        ("likeness", {"slug": "l", "mesh": str(mesh)}),
    )
    assert set(json.loads(spec.content[0].text)["dims"]) == {"width", "depth"}
    assert (jobs / "l" / "spec.json").is_file()
    assert json.loads(likeness.content[0].text)["advice"][0]["severity"] == "note"
    assert likeness.content[1].type == "image"


def test_a_bad_call_is_an_error_not_a_crash(jobs):
    [result] = _call(("measure_dim", {"slug": "l", "name": "width", "view": "front",
                                       "points": [[0, 0], [1, 1]]}))
    assert result.is_error
    assert "no scale yet" in result.content[0].text


@pytest.mark.parametrize("slug", ["../escape", "/tmp/escape", "a/b", "", "UP"])
def test_a_slug_cannot_leave_the_jobs_folder(jobs, slug):
    [result] = _call(("add_unknown", {"slug": slug, "text": "x"}))
    assert result.is_error
    assert not (jobs.parent / "escape").exists()


def test_a_view_name_cannot_leave_the_job(tmp_path, jobs):
    photo = _photo(tmp_path / "photo.png")
    [result] = _call(("measure_rectify", {"slug": "l", "view": "../../../escape", "photo": str(photo),
                                          "corners": [[0, 0], [10, 0], [10, 10], [0, 10]],
                                          "size_mm": [10, 10]}))
    assert result.is_error
    assert not list(tmp_path.rglob("escape.png"))


def test_only_read_tools_skip_the_prompt():
    async def hints():
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools
                    if t.annotations and t.annotations.read_only_hint}

    assert anyio.run(hints) == {"guide", "show_spec", "meet", "grid", "check", "likeness", "view"}
