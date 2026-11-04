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


def test_sign_brief_uses_every_stated_axis():
    # Only the first number used to be read: "40mm x 8mm x 3mm" became (40, 16, 3) - the
    # stated depth was replaced by the nameplate default with no assumption recorded.
    brief = parse_brief("a 40mm x 8mm x 3mm nameplate that says BEN")
    assert brief.intent == "sign"
    assert brief.target_size_mm == (40.0, 8.0, 3.0)
    assert brief.assumptions == ()


def test_sign_brief_takes_two_stated_axes_and_derives_the_third():
    brief = parse_brief("a 60mm by 20mm plaque that says HALL")
    assert brief.target_size_mm == (60.0, 20.0, 3.0)
    assert any("Thickness not stated" in note for note in brief.assumptions)
    assert not any("Depth not stated" in note for note in brief.assumptions)


def test_sign_brief_records_each_derived_axis():
    brief = parse_brief("a 40mm nameplate that says MARCO")
    assert brief.target_size_mm == (40.0, 16.0, 3.0)
    assert any("Depth not stated" in note for note in brief.assumptions)
    assert any("Thickness not stated" in note for note in brief.assumptions)


def test_sign_brief_without_a_size_still_uses_the_default():
    brief = parse_brief("a small nameplate that says TEA")
    assert brief.target_size_mm == (40.0, 16.0, 3.0)
    assert brief.assumptions == ("Default nameplate 40 x 16 x 3 mm.",)


def test_sign_brief_reads_a_dimension_with_one_unit():
    brief = parse_brief("a 40 x 8 x 3 mm nameplate that says BEN")
    assert brief.target_size_mm == (40.0, 8.0, 3.0)
    assert brief.assumptions == ()


def test_sign_brief_ignores_other_mm_values():
    # A letter height or hole size is not an axis of the plate.
    brief = parse_brief("a 60mm sign with 5mm letters and 4mm holes that says HALL")
    assert brief.target_size_mm == (60.0, 24.0, 3.0)
    assert any("Depth not stated" in note for note in brief.assumptions)
