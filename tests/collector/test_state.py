"""StateStore: complete-snapshot gating, last_seen gone, semantic diff, alerts."""
from __future__ import annotations

import logging
import threading
import time

from conftest import Harness, make_status

_KEYS = {"logitech:046d:c547:u1", "razer:1532:0554:1a2b3c4d"}


def _mouse(level=68, charging=False, name="G502 X PLUS"):
    return make_status("logitech:c547:1", name, level, charging=charging,
                       source="logitech", kind="mouse")


def _headset(level=55):
    return make_status("razer:0554:1a2b3c4d", "BlackShark V2 Pro", level,
                       source="razer", kind="headset")


# ---------------------------------------------------------------- gating
def test_nothing_is_merged_until_every_provider_reported(harness):
    harness.begin("logitech", "razer")
    assert harness.report("logitech", [_mouse()]) is False
    assert harness.sink.messages == []          # no device, no tick, no merge
    assert harness.report("razer", [_headset()]) is True
    keys = {m["device"]["key"] for m in harness.sink.by_type("device")}
    assert keys == {"logitech:046d:c547:u1", "razer:1532:0554:1a2b3c4d"}


def test_second_round_waits_for_its_own_providers(harness):
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse()])
    harness.report("razer", [_headset()])
    harness.sink.clear()

    harness.begin("logitech", "razer")
    assert harness.report("razer", [_headset(54)]) is False
    assert harness.sink.by_type("device") == []
    assert harness.report("logitech", [_mouse(64)]) is True
    levels = {m["device"]["key"]: m["device"]["level"]
              for m in harness.sink.by_type("device")}
    assert levels == {"logitech:046d:c547:u1": 64,
                      "razer:1532:0554:1a2b3c4d": 54}


def test_partial_round_never_emits_gone(harness):
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse()])
    harness.report("razer", [_headset()])
    harness.sink.clear()

    # Only one provider answers this round: the round is still open, so the
    # global tick must not decide that anything is gone.
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse()])
    harness.tick(30.0)
    assert harness.sink.by_type("gone") == []
    assert {d["key"] for d in harness.state.snapshot_devices()} == {
        "logitech:046d:c547:u1", "razer:1532:0554:1a2b3c4d"}


def test_results_arriving_out_of_order_and_concurrently_do_not_emit_gone(tmp_path):
    h = Harness(tmp_path, providers=("logitech", "razer"))
    h.begin("logitech", "razer")
    results = {"logitech": [_mouse()], "razer": [_headset()]}
    barrier = threading.Barrier(2)
    threads = []

    def worker(name):
        barrier.wait()                     # both start together, order undefined
        for _ in range(20):
            time.sleep(0)
        h.report(name, results[name])

    for name in ("razer", "logitech"):
        thread = threading.Thread(target=worker, args=(name,))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()

    assert len(h.sink.by_type("device")) == 2
    assert h.sink.by_type("gone") == []
    assert h.state.pending == set()


# ---------------------------------------------------------------- gone
def test_gone_needs_max_two_ttl_or_ten_seconds(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.clock.advance(9.9)
    h.state.on_tick()
    assert h.sink.by_type("gone") == []         # 9.9 s <= max(2*5, 10) = 10 s
    h.clock.advance(0.2)
    h.state.on_tick()
    gone = h.sink.by_type("gone")
    assert {m["key"] for m in gone} == {"logitech:046d:c547:u1",
                                        "razer:1532:0554:1a2b3c4d"}


def test_gone_is_time_based_not_missed_rounds(two_provider_harness):
    """Repeated empty snapshots must not shorten or lengthen the deadline."""
    h = two_provider_harness
    h.sink.clear()
    for _ in range(9):                          # nine empty rounds, 9 seconds
        h.begin("logitech", "razer")
        h.report("logitech", [])
        h.report("razer", [])
        h.clock.advance(1.0)
        h.state.on_tick()
    assert h.sink.by_type("gone") == []
    h.begin("logitech", "razer")
    h.report("logitech", [])
    h.report("razer", [])
    h.clock.advance(1.1)                        # 10.1 s since the last sighting
    h.state.on_tick()
    assert len(h.sink.by_type("gone")) == 2


def test_gone_is_suppressed_while_the_provider_is_in_flight(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.state.set_in_flight({"logitech"})         # e.g. xinput blocked in WGI 20 s
    h.clock.advance(60.0)
    h.state.on_tick()
    assert {m["key"] for m in h.sink.by_type("gone")} == {"razer:1532:0554:1a2b3c4d"}
    assert "logitech:046d:c547:u1" in {d["key"] for d in h.state.snapshot_devices()}
    h.state.set_in_flight(set())
    h.state.on_tick()
    assert {m["key"] for m in h.sink.by_type("gone")} == {"razer:1532:0554:1a2b3c4d",
                                                         "logitech:046d:c547:u1"}


def test_reappearing_provider_refreshes_last_seen(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.clock.advance(9.0)
    h.begin("logitech", "razer")
    h.report("logitech", [_mouse()])
    h.report("razer", [_headset()])
    h.clock.advance(5.0)                        # 14 s after the first sighting ...
    h.state.on_tick()                           # ... but only 5 s after the re-report
    assert h.sink.by_type("gone") == []


# ---------------------------------------------------------------- diff
def test_semantic_changes_only(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.clock.advance(5.0)
    h.begin("logitech", "razer")
    h.report("logitech", [_mouse()])            # identical values
    h.report("razer", [_headset()])
    assert h.sink.by_type("device") == []
    first = h.state.snapshot_devices()

    h.begin("logitech", "razer")
    h.report("logitech", [_mouse(67)])
    h.report("razer", [_headset()])
    events = h.sink.by_type("device")
    assert len(events) == 1 and events[0]["device"]["level"] == 67
    # updated_unix means "last semantic change", so the unchanged device keeps it
    after = {d["key"]: d for d in h.state.snapshot_devices()}
    before = {d["key"]: d for d in first}
    assert after["razer:1532:0554:1a2b3c4d"]["updated_unix"] == \
        before["razer:1532:0554:1a2b3c4d"]["updated_unix"]


# ---------------------------------------------------------------- device fields
def test_null_level_and_approx_are_passed_through(tmp_path):
    h = Harness(tmp_path, providers=("keychron",))
    h.begin("keychron")
    h.report("keychron", [make_status(
        "keychron:0121", "Keychron K2", None, source="keychron", kind="keyboard",
        approx="约 55%（中）")])
    device = h.sink.by_type("device")[0]["device"]
    assert device["level"] is None
    assert device["approx"] == "约 55%（中）"
    assert device["kind"] == "keyboard"
    assert device["name"] == "Keychron K2"
    assert h.sink.by_type("alert") == []        # unknown level must not alert


def test_low_alert_fires_once_and_rearms(harness):
    harness.settings.set("low", 20)
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(12)])
    harness.report("razer", [_headset()])
    alerts = harness.sink.by_type("alert")
    assert [a["kind"] for a in alerts] == ["low"]
    assert alerts[0]["level"] == 12

    harness.sink.clear()
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(11)])    # still low: no second alert
    harness.report("razer", [_headset()])
    assert harness.sink.by_type("alert") == []

    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(30)])    # above low + 5: re-armed
    harness.report("razer", [_headset()])
    assert harness.sink.by_type("alert") == []
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(9)])
    harness.report("razer", [_headset()])
    assert [a["kind"] for a in harness.sink.by_type("alert")] == ["low"]


def test_full_alert_only_after_charging_below_100(harness):
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(100)])   # already full when first seen: no alert
    harness.report("razer", [_headset()])
    assert harness.sink.by_type("alert") == []

    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(80, charging=True)])
    harness.report("razer", [_headset()])
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(100, charging=True)])
    harness.report("razer", [_headset()])
    assert [a["kind"] for a in harness.sink.by_type("alert")] == ["full"]
    count = len(harness.sink.by_type("alert"))
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(100, charging=True)])
    harness.report("razer", [_headset()])
    assert len(harness.sink.by_type("alert")) == count    # once per charge


# ---------------------------------------------------------------- replaces
def test_key_upgrade_emits_replaces_and_no_gone(tmp_path):
    hints = {
        "logitech:c547:1": {"vid": 0x046D, "pid": 0xC547, "name": "G502 X PLUS",
                            "kind": "mouse", "transport": "2.4g"},
        "logitech:0a1b2c3d": {"vid": 0x046D, "pid": 0xC547, "unit_id": "0A1B2C3D",
                              "name": "G502 X PLUS", "kind": "mouse",
                              "transport": "2.4g"},
    }
    h = Harness(tmp_path, providers=("logitech",), hints=hints)
    h.begin("logitech")
    h.report("logitech", [make_status("logitech:c547:1", "G502 X PLUS", 68,
                                      source="logitech", kind="mouse")])
    old_key = h.sink.by_type("device")[0]["device"]["key"]
    assert old_key == "logitech:046d:c547:u1"
    h.sink.clear()

    h.begin("logitech")
    h.report("logitech", [make_status("logitech:0a1b2c3d", "G502 X PLUS", 64,
                                      source="logitech", kind="mouse")])
    events = h.sink.by_type("device")
    assert len(events) == 1
    assert events[0]["replaces"] == old_key
    assert events[0]["device"]["key"] == "logitech:046d:c547:unit-0a1b2c3d"
    assert h.sink.by_type("gone") == []
    assert h.keys.migration_count == 1
    assert h.keys.stable_for("logitech:c547:1") == events[0]["device"]["key"]


def test_hidden_device_is_dropped_but_never_reported_gone(two_provider_harness):
    h = two_provider_harness
    hidden_key = "logitech:046d:c547:u1"
    h.settings.set_hidden(hidden_key, True)
    h.state.refresh_from_settings()
    assert hidden_key not in {d["key"] for d in h.state.snapshot_devices()}
    h.sink.clear()
    h.clock.advance(5.0)                        # below the gone threshold
    h.state.on_tick()
    assert hidden_key not in {m["key"] for m in h.sink.by_type("gone")}
    # the other device is untouched
    assert "razer:1532:0554:1a2b3c4d" in {d["key"] for d in h.state.snapshot_devices()}


# ---------------------------------------------------------------- tick
def test_tick_full_alignment_period(tmp_path):
    h = Harness(tmp_path, providers=("logitech", "razer"), ttl=100.0)
    h.begin("logitech", "razer")
    h.report("logitech", [_mouse()])
    h.report("razer", [_headset()])
    h.sink.clear()
    h.clock.advance(1.0)
    h.state.on_tick()                           # first tick is due immediately
    assert len(h.sink.by_type("tick")) == 1
    h.clock.advance(10.0)
    h.state.on_tick()
    assert len(h.sink.by_type("tick")) == 1
    h.clock.advance(20.0)
    h.state.on_tick()
    assert len(h.sink.by_type("tick")) == 2
    assert {d["key"] for d in h.sink.last("tick")["devices"]} == {
        "logitech:046d:c547:u1", "razer:1532:0554:1a2b3c4d"}


# ---------------------------------------------------------------- merge gate
def test_bluetooth_snapshot_is_deferred_while_a_round_is_open(tmp_path):
    hints = {
        "xinput:0": {"vid": 0x045E, "pid": 0x0B13, "name": "Pad X",
                     "kind": "gamepad", "transport": "usb"},
        "bt:001122334455": {"mac": "001122334455", "name": "BT Headset",
                            "kind": "bluetooth", "transport": "bluetooth"},
    }
    h = Harness(tmp_path, providers=("xinput", "bluetooth"), hints=hints,
                bluetooth_event=True)
    h.begin("xinput")
    bt = make_status("bt:001122334455", "BT Headset", 40, source="bluetooth",
                     kind="bluetooth")
    assert h.state.on_bluetooth_snapshot([bt]) is False   # not ready yet
    assert h.report("xinput", [make_status("xinput:0", "Pad X", 80,
                                           source="xinput", kind="gamepad")]) is True
    assert {d["name"] for d in h.state.snapshot_devices()} == {"Pad X", "BT Headset"}

    h.begin("xinput")
    assert h.state.on_bluetooth_snapshot([bt]) is False   # round still open
    assert {d["name"] for d in h.state.snapshot_devices()} == {"Pad X", "BT Headset"}
    h.report("xinput", [make_status("xinput:0", "Pad X", 79,
                                    source="xinput", kind="gamepad")])


def test_real_legacy_key_change_emits_replaces_without_gone(tmp_path):
    """Real providers (no hints): logitech slot key -> unit-id key, same round.

    This is the P1/P9 fix: the old stable key must be morphed (device+replaces),
    not left to expire for 2*TTL, and the new key inherits the old vid/pid.
    """
    h = Harness(tmp_path, providers=("logitech",), ttl=5.0)
    h.begin("logitech")
    h.report("logitech", [make_status("logitech:c547:1", "G502 X PLUS", 68,
                                      source="logitech", kind="mouse")])
    old_key = h.sink.by_type("device")[0]["device"]["key"]
    assert old_key == "logitech:046d:c547:u1"
    h.sink.clear()

    h.begin("logitech")
    h.report("logitech", [make_status("logitech:0a1b2c3d", "G502 X PLUS", 64,
                                      source="logitech", kind="mouse")])
    events = h.sink.by_type("device")
    assert len(events) == 1
    assert events[0].get("replaces") == old_key
    new_key = events[0]["device"]["key"]
    # the unit key does not carry a pid, so it is inherited from the old alias
    assert new_key == "logitech:046d:c547:unit-0a1b2c3d"
    assert h.sink.by_type("gone") == []
    assert h.keys.migration_count == 1
    assert h.keys.history()[0]["reason"] == "same-round-key-change"
    assert h.keys.stable_for("logitech:c547:1") == new_key
    assert h.keys.stable_for("logitech:0a1b2c3d") == new_key

    # no gone flush for the old key after the TTL window
    h.sink.clear()
    h.clock.advance(60.0)
    h.state.on_tick()
    assert old_key not in {m["key"] for m in h.sink.by_type("gone")}


def test_two_identical_devices_block_the_replaces_guess(tmp_path):
    """Two visible same-name devices => ambiguous => no replaces, no merge."""
    h = Harness(tmp_path, providers=("logitech",), ttl=5.0)
    h.begin("logitech")
    h.report("logitech", [
        make_status("logitech:c547:1", "G502 X PLUS", 68, source="logitech",
                    kind="mouse"),
        make_status("logitech:c548:2", "G502 X PLUS", 55, source="logitech",
                    kind="mouse"),
    ])
    assert len(h.state.snapshot_devices()) == 2
    h.sink.clear()

    h.begin("logitech")
    h.report("logitech", [make_status("logitech:0a1b2c3d", "G502 X PLUS", 64,
                                      source="logitech", kind="mouse")])
    assert h.sink.by_type("gone") == []
    assert all("replaces" not in m for m in h.sink.by_type("device"))
    assert h.keys.migration_count == 0
    keys = {d["key"] for d in h.state.snapshot_devices()}
    # both old visible keys survive and are not merged into the new one
    assert "logitech:046d:c547:u1" in keys
    assert "logitech:046d:c548:u1" in keys
    assert len(keys) == 3


def test_migration_carries_hidden_and_rename(tmp_path):
    """Audit P2-6: a hidden/renamed device must not reappear under a new key."""
    h = Harness(tmp_path, providers=("logitech",), ttl=5.0)
    h.begin("logitech")
    h.report("logitech", [make_status("logitech:c547:1", "G502 X PLUS", 68,
                                      source="logitech", kind="mouse")])
    old = h.sink.by_type("device")[0]["device"]["key"]
    h.settings.set_hidden(old, True)
    h.settings.set_name(old, "客厅鼠标")
    h.state.refresh_from_settings()
    assert h.state.snapshot_devices() == []
    h.sink.clear()

    h.begin("logitech")
    h.report("logitech", [make_status("logitech:0a1b2c3d", "G502 X PLUS", 64,
                                      source="logitech", kind="mouse")])
    new = "logitech:046d:c547:unit-0a1b2c3d"
    assert h.sink.by_type("device") == []            # hidden: no row to morph
    assert h.settings.hidden_keys() == frozenset({new})
    assert h.settings.name_for(new) == "客厅鼠标"
    assert h.settings.name_for(old) == ""
    assert h.keys.migration_count == 1

    # un-hiding the new key shows the device with the migrated name
    h.settings.set_hidden(new, False)
    h.state.refresh_from_settings()
    devices = h.state.snapshot_devices()
    assert len(devices) == 1
    assert devices[0]["key"] == new
    assert devices[0]["name"] == "客厅鼠标"


# --------------------------------------------------- P1-2 fast re-enumeration
def test_xinput_slot_change_is_not_a_new_device(tmp_path):
    h = Harness(tmp_path, providers=("xinput",), ttl=5.0)
    h.begin("xinput")
    h.report("xinput", [make_status("xinput:0", "Xbox controller", 80,
                                    source="xinput", kind="gamepad")])
    key = h.sink.by_type("device")[0]["device"]["key"]
    assert key == "xinput:0000:0000:u1"
    h.sink.clear()

    h.begin("xinput")
    h.report("xinput", [make_status("xinput:1", "Xbox controller", 80,
                                    source="xinput", kind="gamepad")])
    assert h.sink.by_type("device") == []            # same device, same values
    assert h.sink.by_type("gone") == []
    assert h.state.snapshot_devices()[0]["key"] == key
    assert h.keys.stable_for("xinput:0") == h.keys.stable_for("xinput:1") == key
    assert h.keys.migration_count == 0

    # the old slot's key is not aged into a gone device
    h.clock.advance(5.0)
    h.state.on_tick()
    assert h.sink.by_type("gone") == []


# --------------------------------------------------- P2-7 transport
def test_transport_inference_for_real_provider_keys():
    from collector.state import infer_transport
    assert infer_transport(make_status("bt:aabbccddeeff", "WH-1000XM5",
                                       source="bluetooth"), "bluetooth") == "bluetooth"
    assert infer_transport(make_status("ps:0ce6:aabbccddeeff", "DualSense",
                                       source="playstation"), "playstation") == "bluetooth"
    assert infer_transport(make_status("ps:0ce6:usb7&11aa&0&0000", "DualSense",
                                       source="playstation"), "playstation") == "usb"
    assert infer_transport(make_status("steam:1304:2", "Steam Controller",
                                       source="steam"), "steam") == "2.4g"
    assert infer_transport(make_status("xinput:0", "Xbox controller",
                                       source="xinput"), "xinput") == "unknown"
    assert infer_transport(make_status("logitech:0a1b2c3d",
                                       "G502 X PLUS LIGHTSPEED",
                                       source="logitech"), "logitech") == "2.4g"
    assert infer_transport(make_status("razer:0554:1", "Razer USB Headset",
                                       source="razer"), "razer") == "usb"


# --------------------------------------------------- P3-7 no providers
def test_no_enabled_providers_is_ready_immediately(tmp_path):
    h = Harness(tmp_path, providers=())
    h.state.on_tick()
    assert h.state.ready is True
    assert len(h.sink.by_type("tick")) == 1


# --------------------------------------------------- P3-9 full alert first sight
def test_first_sighting_full_and_charging_does_not_alert(harness):
    harness.begin("logitech", "razer")
    harness.report("logitech", [_mouse(100, charging=True)])
    harness.report("razer", [_headset()])
    assert harness.sink.by_type("alert") == []


def test_bluetooth_merge_runs_only_on_complete_snapshot(tmp_path):
    hints = {
        "xinput:0": {"vid": 0x045E, "pid": 0x0B13, "name": "Xbox controller",
                     "kind": "gamepad", "transport": "usb"},
        "bt:001122334455": {"mac": "001122334455", "name": "Xbox controller",
                            "kind": "gamepad", "transport": "bluetooth"},
    }
    h = Harness(tmp_path, providers=("xinput", "bluetooth"), hints=hints, ttl=10.0)
    h.begin("xinput", "bluetooth")
    assert h.report("xinput", [make_status("xinput:0", "Xbox controller", 80,
                                           source="xinput", kind="gamepad")]) is False
    assert h.sink.by_type("device") == []       # gated: no merge yet
    assert h.report("bluetooth", [make_status("bt:001122334455", "Xbox controller",
                                              80, source="bluetooth",
                                              kind="gamepad")]) is True
    devices = h.state.snapshot_devices()
    assert len(devices) == 1                    # dedupe_controllers kept the BT copy
    assert devices[0]["transport"] == "bluetooth"


# --------------------------------------------------- P1-10 sleep/resume clock jumps
def _keepalive_tick(h: Harness, advance: float = 5.0) -> None:
    """One normal 5 s heartbeat: providers re-report, then the global tick runs."""
    h.clock.advance(advance)
    h.begin("logitech", "razer")
    h.report("logitech", [_mouse()])
    h.report("razer", [_headset()])
    h.state.on_tick()


def test_two_minutes_of_normal_ticks_never_emit_gone(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    for _ in range(24):                         # 24 * 5 s = 120 s awake
        _keepalive_tick(h)
    assert h.sink.by_type("gone") == []
    assert h.sink.by_type("device") == []       # identical values: no churn
    assert h.state.stats()["clock_jumps"] == 0
    assert {d["key"] for d in h.state.snapshot_devices()} == _KEYS


def test_resume_clock_jump_suppresses_the_gone_storm(two_provider_harness, caplog):
    """Windows' monotonic clock counts sleep: the first tick after +1 h must not
    expire every device (P1-10). Devices stay visible and the UI gets a tick."""
    h = two_provider_harness
    h.sink.clear()
    h.state.on_tick()                           # anchor the previous-tick clock
    h.sink.clear()
    caplog.set_level(logging.INFO, logger="halo_battery")

    h.clock.advance(3600.0)                     # laptop slept for an hour
    h.state.on_tick()                           # first tick after resume

    assert h.sink.by_type("gone") == []
    assert h.sink.by_type("device") == []
    assert {d["key"] for d in h.state.snapshot_devices()} == _KEYS
    assert h.state.stats()["clock_jumps"] == 1
    assert h.state.clock_jumps == 1
    records = [r.getMessage() for r in caplog.records
               if "clock jump detected" in r.getMessage()]
    assert len(records) == 1
    assert "resume_s=3600.0" in records[0]
    ticks = h.sink.by_type("tick")              # resync snapshot, not a device storm
    assert len(ticks) == 1
    assert {d["key"] for d in ticks[0]["devices"]} == _KEYS


def test_gone_decision_is_back_to_normal_after_the_resume_tick(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.state.on_tick()                           # anchor
    h.clock.advance(3600.0)
    h.state.on_tick()                           # suppressed resume tick
    h.sink.clear()

    for _ in range(10):                         # normal 1 Hz ticks, no new reports
        h.clock.advance(1.0)
        h.state.on_tick()
    assert h.sink.by_type("gone") == []         # 10 s <= max(2*5, 10)
    h.clock.advance(1.0)
    h.state.on_tick()                           # 11 s > the gone deadline
    assert {m["key"] for m in h.sink.by_type("gone")} == _KEYS
    assert h.state.stats()["clock_jumps"] == 1  # the 1 Hz ticks are not jumps


def test_consecutive_normal_ticks_do_not_trigger_resume_detection(two_provider_harness):
    h = two_provider_harness
    h.sink.clear()
    h.state.on_tick()                           # anchor
    for _ in range(30):
        h.clock.advance(1.0)
        h.state.on_tick()
    assert h.state.stats()["clock_jumps"] == 0
    assert h.state.clock_jump_threshold == 5.0  # max(5 s, 3 * 1 Hz scheduler period)

    h.clock.advance(4.9)                        # below the threshold
    h.state.on_tick()
    assert h.state.stats()["clock_jumps"] == 0
    h.clock.advance(5.1)                        # longer than the threshold
    h.state.on_tick()
    assert h.state.stats()["clock_jumps"] == 1
