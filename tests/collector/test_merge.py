"""The upstream merge helpers copied into collector/merge.py (BUILD_PLAN §4.2)."""
from __future__ import annotations

from collector.merge import (dedupe_controllers, device_family,
                             drop_bluetooth_duplicates, merge_bluetooth)
from conftest import make_status


def _xinput(name, level=80, via=""):
    return make_status("xinput:0", name, level, source="xinput", kind="gamepad", via=via)


def _bt(name, level=80, kind="gamepad"):
    return make_status("bt:aabbccddeeff", name, level, source="bluetooth", kind=kind)


def _hid(name, level=80, source="logitech"):
    return make_status(f"{source}:1", name, level, source=source, kind="headset")


def test_device_family_ignores_transport_words():
    assert device_family("Audeze Maxwell Headset") == device_family("Audeze Maxwell BT")
    assert device_family("Audeze Maxwell") == "audeze maxwell"
    assert device_family("G PRO X 2 LIGHTSPEED") == "g pro x 2 lightspeed"


def test_dedupe_controllers_prefers_the_bluetooth_reading():
    results = [_xinput("Xbox controller"), _hid("G502 X PLUS", source="logitech")]
    bt = [_bt("Xbox controller")]
    merged = dedupe_controllers(results, bt)
    assert [st.key for st in merged] == ["logitech:1"]


def test_dedupe_controllers_keeps_a_second_identical_pad():
    results = [_xinput("Xbox controller"), _xinput("Xbox controller 2")]
    bt = [_bt("Xbox controller")]
    merged = dedupe_controllers(results, bt)
    # Families are compared exactly, so the "1"/"2" suffix keeps them apart:
    # only the entry whose family matches the Bluetooth pad is dropped.
    assert [st.name for st in merged] == ["Xbox controller 2"]


def test_dedupe_controllers_drops_a_via_bluetooth_xinput_entry():
    results = [_xinput("GameSir controller", via="bluetooth")]
    merged = dedupe_controllers(results, [_bt("GameSir controller")])
    assert merged == []                           # the BT copy is added by the caller


def test_drop_bluetooth_duplicates_hid_wins_only_when_live():
    logged = set()
    results = [_hid("Audeze Maxwell"), _bt("Audeze Maxwell Headset", kind="headset")]
    kept = drop_bluetooth_duplicates(results, logged)
    assert [st.key for st in kept] == ["logitech:1"]
    assert logged                                   # logged once

    # A sleeping HID device (offline) must not hide the live Bluetooth copy.
    results = [_hid("Audeze Maxwell", level=None), _bt("Audeze Maxwell", kind="headset")]
    kept = drop_bluetooth_duplicates(results, set())
    assert len(kept) == 2


def test_merge_bluetooth_disabled_returns_hid_unchanged():
    results = [_xinput("Xbox controller")]
    bt = [_bt("Xbox controller")]
    assert merge_bluetooth(results, bt, False, set()) == results
    merged = merge_bluetooth(results, bt, True, set())
    assert [st.key for st in merged] == ["bt:aabbccddeeff"]
