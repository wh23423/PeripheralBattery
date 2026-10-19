"""Deterministic fake devices for ``--fake`` (no real hardware needed).

The acceptance scenario (``demo``) exercises everything M0 must demonstrate:

* four device kinds plus a Bluetooth device (mouse / headset / keyboard / gamepad
  / bluetooth);
* ``level: null`` with ``approx`` text, and a null level that later becomes real;
* ``charging`` changes;
* a Logitech key migration (receiver slot -> unit id) that must arrive as
  ``device`` + ``replaces`` rather than delete + insert;
* devices disappearing, which must only ever be reported by the global tick
  after ``max(2 * TTL, 10 s)``.

``FakeWorld`` runs on an accelerated virtual clock (``--speed``) so a scenario
that needs tens of virtual seconds finishes in a few real seconds. Providers
report from separate threads with a small delay so their results arrive out of
order - the complete-snapshot gate has to hold.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .providers.base import DeviceStatus


@dataclass
class FakeDevice:
    """One device as a scenario declares it (plus the identity hints it supplies)."""
    legacy_key: str
    name: str
    level: Optional[int] = None
    charging: bool = False
    online: bool = True
    kind: str = ""
    approx: str = ""
    source: str = ""
    via: str = ""
    vid: Optional[Any] = None
    pid: Optional[Any] = None
    serial: Optional[str] = None
    mac: Optional[str] = None
    unit_id: Optional[str] = None
    transport: str = "unknown"

    def hint(self) -> Dict[str, Any]:
        return {
            "vid": self.vid, "pid": self.pid, "serial": self.serial,
            "mac": self.mac, "unit_id": self.unit_id, "name": self.name,
            "kind": self.kind, "transport": self.transport,
        }

    def status(self) -> DeviceStatus:
        return DeviceStatus(
            key=self.legacy_key, name=self.name, level=self.level,
            charging=self.charging, online=self.online,
            source=self.source or "", approx=self.approx, kind=self.kind,
            via=self.via,
        )


class FakeWorld:
    """A scenario timeline and its providers, on a virtual clock."""

    def __init__(self, scenario: str = "demo", speed: float = 8.0):
        self.scenario = scenario if scenario in SCENARIOS else "demo"
        self.speed = max(0.1, float(speed))
        self._real_start = time.monotonic()
        self._base_mono = self._real_start
        self._base_wall = time.time()
        self.providers: List["FakeProvider"] = []
        self._timeline: Dict[str, List[Tuple[float, List[FakeDevice]]]] = {}
        self._hints: Dict[str, Dict[str, Any]] = {}
        self.duration = 0.0
        self._build(SCENARIOS[self.scenario]())
        self._started = False

    # ------------------------------------------------------------ clock
    def elapsed(self) -> float:
        return (time.monotonic() - self._real_start) * self.speed

    def mono(self) -> float:
        return self._base_mono + self.elapsed()

    def wall(self) -> float:
        return self._base_wall + self.elapsed()

    # ------------------------------------------------------------ build
    def _build(self, states: Sequence[Tuple[float, Dict[str, List[FakeDevice]]]]) -> None:
        for at, providers in states:
            self.duration = max(self.duration, float(at))
            for provider, devices in providers.items():
                self._timeline.setdefault(provider, []).append((float(at), list(devices)))
                for device in devices:
                    self._hints[device.legacy_key] = device.hint()
        for provider in self._timeline:
            # The "slow" scenario is for the shutdown/orphan test: a provider poll
            # blocks for seconds when the process is asked to quit.
            self.providers.append(FakeProvider(provider, self,
                                               delay=5.0 if self.scenario == "slow" else 0.0))
        self.duration += 1.0

    # ------------------------------------------------------------ API
    def provider_names(self) -> List[str]:
        return [p.name for p in self.providers]

    def devices_for(self, provider: str) -> List[FakeDevice]:
        timeline = self._timeline.get(provider)
        if not timeline:
            return []
        now = self.elapsed()
        current: List[FakeDevice] = []
        for at, devices in timeline:
            if at > now:
                break
            current = devices
        return list(current)

    def hints_for(self, provider: str, legacy_key: str) -> Optional[Dict[str, Any]]:
        del provider
        return self._hints.get(legacy_key)

    def diagnostics(self, provider: str) -> List[str]:
        lines = [f"[fake:{provider}] scenario={self.scenario} t={self.elapsed():.1f}s"]
        for device in self.devices_for(provider):
            lines.append(f"  {device.legacy_key} '{device.name}' "
                         f"level={device.level} charging={device.charging} "
                         f"kind={device.kind} transport={device.transport}")
        return lines

    def done(self) -> bool:
        return self.elapsed() >= self.duration


class FakeProvider:
    """A provider whose devices come from the scenario timeline."""

    def __init__(self, name: str, world: FakeWorld, delay: float = 0.0):
        self.name = name
        self.world = world
        self.delay = delay
        self.diagnostics_lines: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        if self.delay:
            time.sleep(self.delay)
        devices = self.world.devices_for(self.name)
        self.diagnostics_lines = [
            f"[fake:{self.name}] {len(devices)} device(s) at t={self.world.elapsed():.1f}s"]
        return [d.status() for d in devices]

    def diagnostics(self) -> List[str]:
        return list(self.diagnostics_lines)


# ---------------------------------------------------------------- scenarios
def _logitech_mouse(level: Optional[int], charging: bool = False,
                    unit: bool = False) -> FakeDevice:
    if unit:
        return FakeDevice(
            legacy_key="logitech:0a1b2c3d", name="G502 X PLUS", level=level,
            charging=charging, kind="mouse", source="logitech",
            vid=0x046D, pid=0xC547, unit_id="0A1B2C3D", transport="2.4g")
    return FakeDevice(
        legacy_key="logitech:c547:1", name="G502 X PLUS", level=level,
        charging=charging, kind="mouse", source="logitech",
        vid=0x046D, pid=0xC547, transport="2.4g")


def _razer_headset(level: Optional[int], charging: bool) -> FakeDevice:
    return FakeDevice(
        legacy_key="razer:0554:1a2b3c4d", name="BlackShark V2 Pro",
        level=level, charging=charging, kind="headset", source="razer",
        vid=0x1532, pid=0x0554, serial="1A2B3C4D", transport="2.4g")


def _keychron(level: Optional[int], approx: str = "") -> FakeDevice:
    return FakeDevice(
        legacy_key="keychron:0121", name="Keychron K2", level=level,
        approx=approx, kind="keyboard", source="keychron",
        vid=0x3434, pid=0x0121, transport="2.4g")


def _xinput(level: Optional[int], approx: str = "") -> FakeDevice:
    return FakeDevice(
        legacy_key="xinput:0", name="Xbox 无线控制器", level=level,
        approx=approx, kind="gamepad", source="xinput",
        vid=0x045E, pid=0x0B13, transport="usb")


def _bluetooth_headset(level: Optional[int]) -> FakeDevice:
    return FakeDevice(
        legacy_key="bt:001122334455", name="WH-1000XM5", level=level,
        kind="bluetooth", source="bluetooth", mac="001122334455",
        transport="bluetooth")


def demo_states() -> List[Tuple[float, Dict[str, List[FakeDevice]]]]:
    """Everything M0 promises, in one timeline (virtual seconds)."""
    return [
        (0.0, {
            "logitech": [_logitech_mouse(68)],
            "razer": [_razer_headset(55, charging=True)],
            "keychron": [_keychron(None, approx="约 55%（中）")],
            "xinput": [_xinput(None, approx="已连接，暂未报告电量")],
            "bluetooth": [_bluetooth_headset(12)],
        }),
        (1.5, {"logitech": [_logitech_mouse(64)]}),
        (2.0, {"keychron": [_keychron(55)]}),
        # the receiver slot key becomes the unit id: device + replaces
        (3.0, {"logitech": [_logitech_mouse(64, unit=True)]}),
        (4.0, {"razer": [_razer_headset(54, charging=False)]}),
        (5.0, {"xinput": []}),
        (8.0, {"bluetooth": []}),
        (12.0, {"logitech": [_logitech_mouse(60, charging=True, unit=True)]}),
        # keep the scenario (and the probe run) alive long enough for the global
        # tick to report the two disappeared devices: gamepad TTL 10 s -> gone at
        # ~max(2*10,10)=20 s after t=5; Bluetooth TTL 10 s -> gone ~20 s after t=8.
        (45.0, {}),
    ]


def static_states() -> List[Tuple[float, Dict[str, List[FakeDevice]]]]:
    """No changes at all: used by --once and smoke tests."""
    return [(0.0, {
        "logitech": [_logitech_mouse(68)],
        "razer": [_razer_headset(55, charging=True)],
        "keychron": [_keychron(52)],
        "xinput": [_xinput(80)],
        "bluetooth": [_bluetooth_headset(41)],
    })]


def migration_states() -> List[Tuple[float, Dict[str, List[FakeDevice]]]]:
    """Only the key migration, for a quick replaces demonstration."""
    return [
        (0.0, {"logitech": [_logitech_mouse(70)]}),
        (2.0, {"logitech": [_logitech_mouse(70, unit=True)]}),
    ]


def slow_states() -> List[Tuple[float, Dict[str, List[FakeDevice]]]]:
    """A normal snapshot, but every provider poll blocks 5 s (shutdown test)."""
    return [
        (0.0, {
            "logitech": [_logitech_mouse(68)],
            "razer": [_razer_headset(55, charging=True)],
            "keychron": [_keychron(52)],
            "xinput": [_xinput(80)],
            "bluetooth": [_bluetooth_headset(41)],
        }),
    ]


SCENARIOS = {
    "demo": demo_states,
    "static": static_states,
    "migration": migration_states,
    "slow": slow_states,
}


def make_world(scenario: str = "demo", speed: float = 8.0) -> FakeWorld:
    return FakeWorld(scenario=scenario, speed=speed)
