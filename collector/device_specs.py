"""Nominal endurance for a few known devices (the cold-start fallback).

Why this exists: ``collector.history`` can only estimate a remaining time after
MIN_SPAN of use and MIN_DROP of percentage points, so right after a fresh install
or a charge every device shows no estimate at all. For a few well-known devices
the vendor publishes a capacity (and sometimes an endurance), or the endurance
is a measured estimate; the UI can then show a clearly marked nominal value
("≈ X 小时 Y 分") instead of nothing.

Powered devices are listed here too. Windows' Bluetooth battery value of a
mains-powered device is static and meaningless (the FlyDigi BS2PRO reports a
constant 94 %), so it must be shown as "外接供电" instead of a remaining time.

The catalogue is deliberately small and additive: append a :class:`DeviceSpec`
to :data:`SPECS`; lookup matches it case-insensitively as a substring of the
device name (or the stable key). ``hours`` is the full-charge endurance at
*typical wireless use* - not a datasheet maximum - and ``note`` records why a
value is an estimate rather than a vendor figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

LUNA33_SOURCE = ("https://www.voptronics.com/lunafury-luna33-ultra-lightweight-"
                 "8k-wireless-gaming-mouse.html")
BS2PRO_SOURCE = ("https://www.scythe.co.jp/product/gaming-accessories/"
                 "accessories/bs2pro/")


@dataclass(frozen=True)
class DeviceSpec:
    """One catalogue entry. All strings are ASCII except ``note``."""

    key: str                          # catalogue id, e.g. "lunafury:luna33-w"
    match: Tuple[str, ...]            # case-insensitive substrings of the name
    hours: Optional[float] = None     # full-charge endurance at typical use
    powered: bool = False             # mains-powered: no battery endurance
    capacity_mah: Optional[float] = None
    source: str = ""                  # where capacity/endurance came from
    note: str = ""                    # why an estimate; explicit about guesses


SPECS: Tuple[DeviceSpec, ...] = (
    DeviceSpec(
        key="lunafury:luna33-w",
        match=("luna33", "luna 33"),
        hours=40.0,
        capacity_mah=250.0,
        source=LUNA33_SOURCE,
        note=("ESTIMATE, not a vendor figure: 250 mAh at a typical ~6 mA 2.4 GHz "
              "1 kHz wireless draw -> 250 / 6 ~= 40 h. LunaFury publishes the "
              "capacity but no official runtime."),
    ),
    DeviceSpec(
        key="flydigi:bs2pro",
        match=("bs2pro", "bs2 pro"),
        powered=True,
        source=BS2PRO_SOURCE,
        note=("USB-C powered laptop cooler (mains, no battery endurance). Windows "
              "still exposes a static 94 % BLE 'battery'; it must never be turned "
              "into a remaining time."),
    ),
)


def lookup(name: str, key: str = "") -> Optional[DeviceSpec]:
    """The catalogue entry whose keyword occurs in ``name`` or ``key``.

    Case-insensitive and substring-based so that "LunaFury Luna33-W" matches
    "luna33". Returns None when the device is not in the catalogue - callers
    must keep showing no estimate in that case, never a guessed one.
    """
    haystack = f"{name or ''} {key or ''}".lower()
    for spec in SPECS:
        if any(word in haystack for word in spec.match):
            return spec
    return None


def seconds_left(spec: Optional[DeviceSpec], level: Optional[int]) -> Optional[int]:
    """Nominal seconds left at ``level`` percent, or None when not applicable.

    None for an unknown spec, a mains-powered device or an unknown level. The
    level is clamped to 0..100 (a provider that reports out of range must not
    produce a negative or over-capacity estimate).
    """
    if spec is None or spec.powered or spec.hours is None or level is None:
        return None
    clamped = max(0, min(100, int(level)))
    return int(round(clamped / 100.0 * float(spec.hours) * 3600.0))
