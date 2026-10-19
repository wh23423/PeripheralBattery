"""The nominal-endurance catalogue: cold-start fallback for known devices."""
from __future__ import annotations

from collector import device_specs


def test_catalogue_lookup_is_case_insensitive_substring():
    spec = device_specs.lookup("LunaFury Luna33-W", "lunafury")
    assert spec is not None and spec.key == "lunafury:luna33-w"
    assert device_specs.lookup("LUNAFURY LUNA 33 ULTRA") is not None
    assert device_specs.lookup("FlyDigi BS2PRO") is not None
    assert device_specs.lookup("bs2 pro", "bt:aabbccddeeff") is not None
    # not in the catalogue: never guess an endurance
    assert device_specs.lookup("G502 X PLUS LIGHTSPEED", "logitech:046d:c547:1") is None
    assert device_specs.lookup("") is None


def test_luna33_nominal_endurance_is_documented_as_an_estimate():
    spec = device_specs.lookup("LunaFury Luna33-W")
    assert spec.hours == 40.0
    assert spec.capacity_mah == 250.0
    assert spec.powered is False
    assert "ESTIMATE" in spec.note
    assert "250 mAh" in spec.note
    assert spec.source.startswith("https://")


def test_nominal_seconds_left_scales_with_level_and_clamps():
    spec = device_specs.lookup("LunaFury Luna33-W")
    assert device_specs.seconds_left(spec, 100) == 40 * 3600
    assert device_specs.seconds_left(spec, 70) == int(round(0.70 * 40 * 3600))
    assert device_specs.seconds_left(spec, 0) == 0
    assert device_specs.seconds_left(spec, 150) == 40 * 3600     # clamped
    assert device_specs.seconds_left(spec, -5) == 0              # clamped
    assert device_specs.seconds_left(spec, None) is None


def test_mains_device_has_no_endurance_and_is_flagged_powered():
    spec = device_specs.lookup("FlyDigi BS2PRO")
    assert spec is not None
    assert spec.powered is True
    assert spec.hours is None
    assert "94" in spec.note            # the static Windows value is called out
    assert device_specs.seconds_left(spec, 94) is None
    assert device_specs.seconds_left(None, 50) is None


def test_specs_are_unique_and_matchable():
    ids = [spec.key for spec in device_specs.SPECS]
    assert len(ids) == len(set(ids))
    for spec in device_specs.SPECS:
        assert spec.match, spec.key
        assert device_specs.lookup(spec.match[0]) is spec
