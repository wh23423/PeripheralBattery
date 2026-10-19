"""Stable keys: format, persistence, ordinals, migrations (protocol.md §3)."""
from __future__ import annotations

import json

from collector.keys import KeyStore, build_identity, parse_legacy
from conftest import FakeClock, make_status


def test_key_format_and_suffix_priority():
    hints = {"vid": 0x046D, "pid": 0xC547, "serial": "AB-12:CD", "unit_id": "0A1B2C3D"}
    identity = build_identity("logitech", "logitech:c547:1", hints)
    assert identity.serial == "ab12cd"          # lower case, separators stripped
    assert identity.hw_key() == "logitech:046d:c547:ab12cd"

    mac_identity = build_identity(
        "playstation", "ps:0ce6:AA:BB:CC:DD:EE:FF",
        {"vid": 0x054C, "pid": 0x0CE6, "mac": "AA:BB:CC:DD:EE:FF"})
    assert mac_identity.hw_key() == "playstation:054c:0ce6:aabbccddeeff"


def test_legacy_key_parsers_extract_identity():
    assert parse_legacy("bluetooth", "bt:001122334455").mac == "001122334455"
    assert parse_legacy("playstation", "ps:0ce6:deadbeef").serial == "deadbeef"
    assert parse_legacy("playstation", "ps:0ce6:aabbccddeeff").mac == "aabbccddeeff"
    assert parse_legacy("logitech", "logitech:0A1B2C3D").unit == "0a1b2c3d"
    assert parse_legacy("logitech", "logitech:c52b:1").pid == "c52b"
    assert parse_legacy("8bitdo", "8bitdo:1234:SN12345").serial == "sn12345"
    assert parse_legacy("nintendo", "switch:2009:aabbccddeeff").mac == "aabbccddeeff"
    assert parse_legacy("pulsar", "pulsar:3710abcd").vid == "3710"
    assert parse_legacy("pulsar", "pulsar:3710abcd").pid == "abcd"


def test_stable_key_never_uses_slot_portable_ordinal():
    store = KeyStore(None, clock=FakeClock())
    first = store.resolve("xinput", "xinput:0",
                          make_status("xinput:0", "Xbox wireless controller",
                                      source="xinput", kind="gamepad"),
                          hints={"vid": 0x045E, "pid": 0x0B13},
                          live_legacy={"xinput:0"})
    second = store.resolve("xinput", "xinput:1",
                           make_status("xinput:1", "Xbox wireless controller 2",
                                       source="xinput", kind="gamepad"),
                           hints={"vid": 0x045E, "pid": 0x0B13},
                           live_legacy={"xinput:0", "xinput:1"})
    assert first.stable == "xinput:045e:0b13:u1"
    assert second.stable == "xinput:045e:0b13:u2"
    assert first.stable != "xinput:0"
    assert first.stable.split(":")[-1].startswith("u")   # ordinal suffix only
    assert first.replaces is None


def test_key_survives_restart_and_reordering(tmp_path):
    path = str(tmp_path / "aliases.json")
    clock = FakeClock()
    store = KeyStore(path, clock=clock)
    stable = store.resolve("keychron", "keychron:0121",
                           make_status("keychron:0121", "Keychron K2"),
                           hints={"vid": 0x3434, "pid": 0x0121}).stable
    store.save(force=True)

    # "restart": a new store reads the same file; resolution order changes but the
    # key must not.
    restarted = KeyStore(path, clock=clock)
    again = restarted.resolve("keychron", "keychron:0121",
                              make_status("keychron:0121", "Keychron K2"),
                              hints={"vid": 0x3434, "pid": 0x0121})
    assert again.stable == stable
    assert again.replaces is None
    assert again.stable == "keychron:3434:0121:u1"


def test_two_devices_get_distinct_ordinals_and_keep_them(tmp_path):
    path = str(tmp_path / "aliases.json")
    store = KeyStore(path, clock=FakeClock())
    a = store.resolve("logitech", "logitech:c547:1",
                      make_status("logitech:c547:1", "Mouse A")).stable
    b = store.resolve("logitech", "logitech:c548:2",
                      make_status("logitech:c548:2", "Mouse B")).stable
    assert a != b
    store.save(force=True)
    restarted = KeyStore(path, clock=FakeClock())
    assert restarted.stable_for("logitech:c547:1") == a
    assert restarted.stable_for("logitech:c548:2") == b


def test_logitech_slot_to_unit_migration_records_history(tmp_path):
    path = str(tmp_path / "aliases.json")
    clock = FakeClock()
    store = KeyStore(path, clock=clock)
    hints_slot = {"vid": 0x046D, "pid": 0xC547, "name": "G502 X PLUS",
                  "kind": "mouse", "transport": "2.4g"}
    old = store.resolve("logitech", "logitech:c547:1",
                        make_status("logitech:c547:1", "G502 X PLUS"),
                        hints=hints_slot)
    assert old.stable == "logitech:046d:c547:u1"

    hints_unit = {"vid": 0x046D, "pid": 0xC547, "unit_id": "0A1B2C3D",
                  "name": "G502 X PLUS", "kind": "mouse", "transport": "2.4g"}
    new = store.resolve("logitech", "logitech:0a1b2c3d",
                        make_status("logitech:0a1b2c3d", "G502 X PLUS"),
                        hints=hints_unit, live_legacy={"logitech:0a1b2c3d"})
    assert new.stable == "logitech:046d:c547:unit-0a1b2c3d"
    assert new.replaces == old.stable
    assert store.migration_count == 1
    history = store.history()
    assert history[0]["from"] == old.stable
    assert history[0]["to"] == new.stable
    assert history[0]["legacy"] == "logitech:0a1b2c3d"
    # the old upstream key now maps to the upgraded key, so P9 sees one device
    assert store.stable_for("logitech:c547:1") == new.stable

    # persisted: a restart does not migrate again
    store.save(force=True)
    restarted = KeyStore(path, clock=clock)
    assert restarted.migration_count == 1
    assert restarted.stable_for("logitech:0a1b2c3d") == new.stable
    data = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["history"][0]["reason"] == "identity-upgraded"


def test_same_name_two_live_devices_are_not_collapsed(tmp_path):
    """Two controllers with the same name: the second gets its own ordinal."""
    store = KeyStore(None, clock=FakeClock())
    hints = {"vid": 0x28DE, "pid": 0x1304, "name": "Steam Controller"}
    a = store.resolve("steam", "steam:1304:2", None, hints=hints)
    b = store.resolve("steam", "steam:1304:3", None, hints=hints,
                      live_legacy={"steam:1304:2", "steam:1304:3"})
    assert a.stable != b.stable
    assert a.stable == "steam:28de:1304:u1"
    assert b.stable == "steam:28de:1304:u2"


def test_mac_and_serial_win_over_ordinal(tmp_path):
    store = KeyStore(tmp_path / "aliases.json", clock=FakeClock())
    bt = store.resolve("bluetooth", "bt:001122334455",
                       make_status("bt:001122334455", "WH-1000XM5"))
    assert bt.stable == "bluetooth:0000:0000:001122334455"
    ps = store.resolve("playstation", "ps:0ce6:aabbccddeeff",
                       make_status("ps:0ce6:aabbccddeeff", "DualSense"))
    assert ps.stable == "playstation:054c:0ce6:aabbccddeeff"


# --------------------------------------------------- P1-2 fast re-enumeration
def test_xinput_slot_change_reuses_the_same_ordinal(tmp_path):
    """Audit repro9: slot 0 -> slot 1 ten seconds later must NOT become u2."""
    clock = FakeClock()
    store = KeyStore(tmp_path / "aliases.json", clock=clock)
    status = make_status("xinput:0", "Xbox wireless controller",
                         source="xinput", kind="gamepad")
    first = store.resolve("xinput", "xinput:0", status, now=clock())
    assert first.stable == "xinput:0000:0000:u1"

    clock.advance(10.0)
    moved = store.resolve("xinput", "xinput:1",
                          make_status("xinput:1", "Xbox wireless controller",
                                      source="xinput", kind="gamepad"),
                          now=clock(), live_legacy={"xinput:1"})
    assert moved.stable == "xinput:0000:0000:u1"
    assert moved.replaces is None
    assert store.migration_count == 0
    assert store.stable_for("xinput:0") == "xinput:0000:0000:u1"
    assert store.stable_for("xinput:1") == "xinput:0000:0000:u1"

    # back to slot 0 later: still the same key, no churn in either direction
    clock.advance(200.0)
    back = store.resolve("xinput", "xinput:0", status, now=clock(),
                         live_legacy={"xinput:0"})
    assert back.stable == "xinput:0000:0000:u1"


def test_steam_interface_change_reuses_the_same_ordinal(tmp_path):
    clock = FakeClock()
    store = KeyStore(None, clock=clock)
    first = store.resolve("steam", "steam:1304:2",
                          make_status("steam:1304:2", "Steam Controller",
                                      source="steam", kind="gamepad"),
                          now=clock())
    clock.advance(5.0)
    moved = store.resolve("steam", "steam:1304:3",
                          make_status("steam:1304:3", "Steam Controller",
                                      source="steam", kind="gamepad"),
                          now=clock(), live_legacy={"steam:1304:3"})
    assert moved.stable == first.stable
    assert moved.replaces is None


def test_xinput_name_suffix_does_not_split_the_fingerprint(tmp_path):
    clock = FakeClock()
    store = KeyStore(None, clock=clock)
    first = store.resolve("xinput", "xinput:0",
                          make_status("xinput:0", "Xbox controller 1",
                                      source="xinput", kind="gamepad"))
    # a single controller loses the " 1" suffix after the second one disconnects
    second = store.resolve("xinput", "xinput:1",
                           make_status("xinput:1", "Xbox controller",
                                       source="xinput", kind="gamepad"),
                           live_legacy={"xinput:1"})
    assert second.stable == first.stable


def test_two_devices_swapping_slots_do_not_collapse_into_one(tmp_path):
    clock = FakeClock()
    store = KeyStore(None, clock=clock)
    a = store.resolve("xinput", "xinput:0",
                      make_status("xinput:0", "Xbox controller 1",
                                  source="xinput", kind="gamepad"))
    b = store.resolve("xinput", "xinput:1",
                      make_status("xinput:1", "Xbox controller 2",
                                  source="xinput", kind="gamepad"),
                      live_legacy={"xinput:0", "xinput:1"})
    assert a.stable != b.stable
    # both aliases are live in the current snapshot: the other one must win, so
    # a third slot key is a genuinely new identity, not a silent merge
    third = store.resolve("xinput", "xinput:2",
                          make_status("xinput:2", "Xbox controller 1",
                                      source="xinput", kind="gamepad"),
                          live_legacy={"xinput:0", "xinput:1", "xinput:2"})
    assert third.stable not in (a.stable, b.stable)


# --------------------------------------------------- P2-5 hw-key -> hw-key
def test_hw_key_change_emits_replaces(tmp_path):
    """PlayStation USB instance changes with a port: morph instead of churn."""
    store = KeyStore(None, clock=FakeClock())
    old = store.resolve("playstation", "ps:0ce6:usb7&11aa&0&0000",
                        make_status("ps:0ce6:usb7&11aa&0&0000", "DualSense",
                                    source="playstation", kind="gamepad"),
                        live_legacy={"ps:0ce6:usb7&11aa&0&0000"})
    assert old.replaces is None
    assert "&" not in old.stable
    new = store.resolve("playstation", "ps:0ce6:usb9&22bb&0&0000",
                        make_status("ps:0ce6:usb9&22bb&0&0000", "DualSense",
                                    source="playstation", kind="gamepad"),
                        live_legacy={"ps:0ce6:usb9&22bb&0&0000"},
                        visible_stables={old.stable})
    assert new.replaces == old.stable
    assert store.migration_count == 1
    assert store.stable_for("ps:0ce6:usb9&22bb&0&0000") == new.stable


def test_key_suffix_is_ndjson_checker_safe(tmp_path):
    store = KeyStore(None, clock=FakeClock())
    resolved = store.resolve("playstation", "ps:0ce6:usb7&11aa&0&0000",
                             make_status("ps:0ce6:usb7&11aa&0&0000", "DualSense",
                                         source="playstation", kind="gamepad"),
                             live_legacy={"ps:0ce6:usb7&11aa&0&0000"})
    suffix = resolved.stable.rsplit(":", 1)[-1]
    assert suffix and all(c.isalnum() for c in suffix)
    assert "&" not in resolved.stable and "#" not in resolved.stable
