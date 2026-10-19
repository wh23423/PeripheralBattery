"""The upstream merge helpers, kept verbatim (BUILD_PLAN §4.2).

``dedupe_controllers`` and ``drop_bluetooth_duplicates`` both require the
*complete* HID set of one poll: they compare every device against every other.
The collector therefore runs them only on a gated full snapshot, never on an
incremental provider result.

The bodies below mirror ``hb/halo_battery.pyw`` (same log lines, same
tie-breaks). They deliberately operate on the raw ``providers.base.DeviceStatus``
objects *before* the stable key rewrite, because they recognise Bluetooth
entries by the upstream ``bt:`` key prefix / ``source`` field.
"""
from __future__ import annotations

import logging
from typing import List, Set

from .providers.base import DeviceStatus

log = logging.getLogger("halo_battery")

GAMEPAD_WORDS = ("controller", "gamepad", "joystick", "joy-con")

# Words that describe how a device is connected or what shape it is, rather than
# which device it is (upstream comment kept).
_TRANSPORT_WORDS = frozenset((
    "bt", "ble", "bluetooth", "wireless", "dongle", "receiver", "headset", "usb",
))


def device_family(name: str) -> str:
    """A device name reduced to what identifies the device, not the connection.

    "Audeze Maxwell", "Audeze Maxwell Headset" and "Audeze Maxwell BT" are one
    headset seen over three connections and have to come out equal.
    """
    words = "".join(c if c.isalnum() else " " for c in (name or "").lower()).split()
    return " ".join(w for w in words if w not in _TRANSPORT_WORDS)


def dedupe_controllers(results: List[DeviceStatus],
                       bt: List[DeviceStatus]) -> List[DeviceStatus]:
    """A controller connected over Bluetooth is seen twice: by the controller
    provider and as a Bluetooth device. Windows' own Bluetooth battery value is
    the one shown in Settings, so the controller provider's entry is dropped."""
    bt_pads = [s for s in bt if s.kind == "gamepad"
               or any(w in s.name.lower() for w in GAMEPAD_WORDS)]
    if not bt_pads:
        return results
    families = {f for f in (device_family(s.name) for s in bt_pads) if f}
    out: List[DeviceStatus] = []
    for s in results:
        if s.source != "xinput":
            out.append(s)
            continue
        if s.via == "bluetooth":
            log.info("[XInput] %s is connected over Bluetooth and shown as a Bluetooth device",
                     s.name)
            continue
        fam = device_family(s.name)
        if fam and fam in families:
            log.info("[XInput] %s: the Bluetooth reading of the same controller is shown instead",
                     s.name)
            continue
        out.append(s)
    return out


def drop_bluetooth_duplicates(results: List[DeviceStatus],
                              logged: Set[str]) -> List[DeviceStatus]:
    """One icon per device, not one per transport (HID reading wins)."""
    hid = [device_family(st.name) for st in results
           if not st.key.startswith("bt:") and st.source not in ("bluetooth", "xinput")
           and st.online and st.level is not None]
    kept: List[DeviceStatus] = []
    for st in results:
        if st.key.startswith("bt:") or st.source == "bluetooth":
            fam = device_family(st.name)
            duplicate = bool(fam) and any(
                fam == h or (min(len(fam), len(h)) >= 6 and (fam in h or h in fam))
                for h in hid)
            if duplicate:
                if st.key not in logged:
                    logged.add(st.key)
                    log.info("[Bluetooth] %s is already read over HID, the "
                             "Bluetooth copy is not shown", st.name)
                continue
            logged.discard(st.key)
        kept.append(st)
    return kept


def merge_bluetooth(hid: List[DeviceStatus], bt: List[DeviceStatus],
                    enabled: bool, logged: Set[str]) -> List[DeviceStatus]:
    """``Collector.merge_bluetooth`` from upstream, as a free function.

    ``hid`` is this round's complete vendor-provider result, ``bt`` the latest
    complete Bluetooth snapshot (the watcher cache upstream).
    """
    if not enabled:
        return list(hid)
    results = dedupe_controllers(list(hid), list(bt)) + list(bt)
    return drop_bluetooth_duplicates(results, logged)
