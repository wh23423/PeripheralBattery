"""Shared fixtures for the collector tests.

The tests drive ``StateStore`` directly with a fake monotonic/wall clock, so
"10 seconds passed" is deterministic and the suite never sleeps for TTLs.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:                     # `python -m pytest tests/collector -q`
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from collector.keys import KeyStore  # noqa: E402
from collector.protocol import RecordingSink  # noqa: E402
from collector.providers.base import DeviceStatus  # noqa: E402
from collector.settings import Settings  # noqa: E402
from collector.state import StateStore  # noqa: E402

WALL_BASE = 1_700_000_000.0


class FakeClock:
    """Monotonic fake clock with a matching wall clock for updated_unix."""

    def __init__(self, start: float = 1000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def wall(self) -> float:
        return WALL_BASE + self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def make_status(key: str, name: str, level=None, charging: bool = False,
                online: bool = True, source: str = "logitech", approx: str = "",
                kind: str = "", via: str = "", transport: str = "") -> DeviceStatus:
    return DeviceStatus(key=key, name=name, level=level, charging=charging,
                        online=online, source=source, approx=approx, kind=kind,
                        via=via, transport=transport)


class Harness:
    """A StateStore wired to a temp data dir, recording sink and fake clock."""

    def __init__(self, tmp_path, providers=("logitech", "razer"), ttl: float = 5.0,
                 hints=None, bluetooth_event: bool = False, history=None,
                 tick_interval: float = 30.0):
        self.clock = FakeClock()
        self.settings = Settings(str(tmp_path / "data"))
        self.settings.ensure_dirs()
        self.settings.set("tick_interval", float(tick_interval))
        self.keys = KeyStore(str(tmp_path / "data" / "aliases.json"), clock=self.clock)
        self.sink = RecordingSink()
        self.hints = dict(hints or {})
        self.ttl = float(ttl)
        self.state = StateStore(
            providers=list(providers), sink=self.sink, keys=self.keys,
            settings=self.settings,
            hints_for=lambda provider, legacy: self.hints.get(legacy),
            history=history,
            event_providers=("bluetooth",) if bluetooth_event else (),
            clock=self.clock, wallclock=self.clock.wall)
        self.state.set_ttl_for(lambda provider, devices: self.ttl)

    # -- shortcuts ---------------------------------------------------------
    def begin(self, *providers: str) -> None:
        self.state.begin_round(providers or tuple(self.state._gated))

    def report(self, provider: str, devices) -> bool:
        return self.state.on_provider_result(provider, list(devices))

    def tick(self, advance: float = 0.0) -> list:
        if advance:
            self.clock.advance(advance)
        before = len(self.sink.messages)
        self.state.on_tick()
        return self.sink.messages[before:]

    def devices(self):
        return {m["device"]["key"]: m["device"] for m in self.sink.by_type("device")}

    def gone(self):
        return [m["key"] for m in self.sink.by_type("gone")]


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


@pytest.fixture
def two_provider_harness(tmp_path):
    h = Harness(tmp_path, providers=("logitech", "razer"))
    h.begin("logitech", "razer")
    h.report("logitech", [make_status("logitech:c547:1", "G502 X PLUS", 68,
                                      source="logitech", kind="mouse")])
    h.report("razer", [make_status("razer:0554:1a2b3c4d", "BlackShark V2 Pro", 55,
                                   source="razer", kind="headset")])
    return h
