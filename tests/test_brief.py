from print3d.brief import parse_brief


def test_cube_brief():
    brief = parse_brief("20 mm calibration cube")
    assert brief.intent == "utility"
    assert brief.target_size_mm == (20.0, 20.0, 20.0)
    assert brief.material == "PLA"


def test_nameplate_brief():
    brief = parse_brief("a 40mm nameplate that says MARCO in green")
    assert brief.intent == "sign"
    assert brief.text == "MARCO"
    assert brief.color_name == "green"
    assert brief.slug == "marco"
    assert brief.target_size_mm[0] == 40.0


