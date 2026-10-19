"""LunaFury provider: the WLmouse/LAMZU-family exchange on the Luna33 ids.

The replies below are the ones a Luna33-W actually returned (373E:0033):
``00 a1 00 02 02 00 83 00 18`` - not charging, 24 %.
"""
from __future__ import annotations

import pytest

from collector.providers import lunafury
from collector.providers.lunafury import LunafuryProvider

WIRELESS = 0x0033
WIRED = 0x0032
VID = 0x373E


def reply(level: int, charging: bool = False) -> list:
    return [0x00, 0xA1, 0x00, 0x02, 0x02, 0x00, 0x83,
            int(charging), int(level)] + [0x00] * 56


class FakeDevice:
    def __init__(self, replies, send_error=None):
        self.replies = list(replies)
        self.send_error = send_error
        self.sent = []
        self.opened = None
        self.closed = False

    def open_path(self, path):
        self.opened = path

    def send_feature_report(self, report):
        self.sent.append(list(report))
        if self.send_error is not None:
            raise self.send_error
        return len(report)

    def get_feature_report(self, report_id, length):
        if not self.replies:
            return None
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class FakeHid:
    def __init__(self, device_factory):
        self._factory = device_factory
        self.devices = []

    def device(self):
        dev = self._factory()
        self.devices.append(dev)
        return dev


def iface(pid: int, interface: int, usage: int, product: str = "Luna33-W",
          manufacturer: str = "LunaFury") -> dict:
    return {
        "vendor_id": VID,
        "product_id": pid,
        "interface_number": interface,
        "usage_page": 0xFFFF,
        "usage": usage,
        "product_string": product,
        "manufacturer_string": manufacturer,
        "path": f"path-{pid}-{interface}".encode(),
        "serial_number": "",
    }


@pytest.fixture
def patch(monkeypatch):
    """Install a fake hidapi and HID list; return a helper to update them."""

    def install(infos, devices):
        fake_hid = FakeHid(lambda: devices.pop(0))
        monkeypatch.setattr(lunafury, "hid", fake_hid)
        monkeypatch.setattr(lunafury.hidlist, "enumerate", lambda vid=0: list(infos))
        return fake_hid

    return install


def test_wireless_level_and_name(patch):
    device = FakeDevice([reply(24)])
    patch([iface(WIRELESS, 1, 0x0001), iface(WIRELESS, 2, 0x0000)], [device])

    found = LunafuryProvider().poll()

    assert len(found) == 1
    status = found[0]
    assert status.key == "lunafury"
    assert status.name == "LunaFury Luna33-W"
    assert status.level == 24
    assert status.charging is False
    assert status.online is True
    assert status.source == "lunafury"
    assert status.kind == "mouse"
    # usage 0 (interface 2) is asked first, so the failing collection is never opened
    assert device.opened == b"path-51-2"
    assert device.sent and device.sent[0][:7] == [0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83]
    assert device.closed is True


def test_charging_flag(patch):
    patch([iface(WIRELESS, 2, 0x0000)], [FakeDevice([reply(80, charging=True)])])

    status = LunafuryProvider().poll()[0]

    assert (status.level, status.charging) == (80, True)


def test_cable_wins_when_both_are_present(patch):
    wired = FakeDevice([reply(55, charging=True)])
    wireless = FakeDevice([reply(54)])
    # the cable is enumerated after the dongle: the provider still reads it first
    patch([iface(WIRELESS, 2, 0x0000), iface(WIRED, 2, 0x0000, product="Luna33")],
          [wired, wireless])

    found = LunafuryProvider().poll()

    assert len(found) == 1
    assert (found[0].level, found[0].charging) == (55, True)
    assert found[0].name == "LunaFury Luna33-W"     # stable through the cable
    assert wireless.opened is None                 # charging source found, rest skipped


def test_silent_mouse_keeps_the_last_level_greyed_out(patch):
    patch([iface(WIRELESS, 2, 0x0000)], [FakeDevice([reply(24)])])
    provider = LunafuryProvider()
    assert provider.poll()[0].level == 24

    patch([iface(WIRELESS, 2, 0x0000)], [FakeDevice([])])   # no reply at all
    status = provider.poll()[0]

    assert (status.level, status.online) == (24, False)


def test_unknown_pid_is_picked_up_by_manufacturer(patch):
    patch([iface(0x0099, 2, 0x0000, product="Luna33 Pro")],
          [FakeDevice([reply(42)])])

    status = LunafuryProvider().poll()[0]

    assert status.level == 42
    assert status.name == "LunaFury Luna33 Pro"


def test_other_vendor_devices_are_ignored(patch):
    # the 373E vendor id is shared with LAMZU (Maya X: 0x001E, read by `lamzu`)
    patch([iface(0x001E, 2, 0x0000, product="Maya X", manufacturer="LAMZU")], [])

    assert LunafuryProvider().poll() == []


def test_send_error_falls_through_to_the_next_collection(patch):
    bad = FakeDevice([], send_error=OSError("Incorrect function"))
    good = FakeDevice([reply(30)])
    patch([iface(WIRELESS, 2, 0x0000), iface(WIRELESS, 1, 0x0001)],
          [bad, good])

    status = LunafuryProvider().poll()[0]

    assert status.level == 30
    assert bad.opened == b"path-51-2" and good.opened == b"path-51-1"


def test_known_name_is_used_when_the_product_string_is_missing(patch):
    patch([iface(WIRELESS, 2, 0x0000, product="")], [FakeDevice([reply(24)])])

    status = LunafuryProvider().poll()[0]

    assert status.name == "LunaFury Luna33-W"


def test_provider_is_registered_everywhere():
    """Missing one registration is the usual way a new provider silently does nothing."""
    from collector.collector import make_providers
    from collector.keys import PROVIDER_VID
    from collector.settings import PROVIDER_CATEGORY

    providers = {p.name: p for p in make_providers()}
    assert "lunafury" in providers
    assert PROVIDER_CATEGORY["lunafury"] == "mouse"
    assert PROVIDER_VID["lunafury"] == "373e"
