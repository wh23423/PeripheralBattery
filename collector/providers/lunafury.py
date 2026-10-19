"""LunaFury mice on the 2.4 GHz dongle (373E:0033) or on the cable (373E:0032).

The exchange is the one the WLmouse, LAMZU and G-Wolves providers use - the same
firmware family - on a vendor collection (usage page 0xFFFF) of the receiver:

    request   feature report 0, 64 bytes: 00 00 02 02 00 83 00 ...
    reply     feature report 0, 65 bytes: 00 a1 00 02 02 00 83 <charging> <battery %>

Confirmed on a Luna33-W (373E:0033) with the vendor's own web driver closed: the
reply was ``00 a1 00 02 02 00 83 00 18`` - not charging, 24 %. The wired pid is
the one the vendor web driver (mouse.lunafury.games) registers in its WebHID
filters, so on the cable the same request is sent to 373E:0032 and the charging
reading wins (one icon per mouse, like the WLmouse provider).

LunaFury shares the 373E vendor id with LAMZU, whose Maya X ids are read by the
``lamzu`` provider; the two never overlap because each lists its own pids. A
model the vendor ships later is still picked up when its manufacturer string says
LunaFury, so only the pid list has to grow, not the provider.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .wlmouse import QUERY, parse_feature

LUNAFURY_VID = 0x373E
WIRELESS = 0x0033
WIRED = 0x0032
KNOWN_PIDS = {WIRELESS: "wireless", WIRED: "cable"}

# Display names for the known ids, used when the device reports no product
# string. The Luna33-W is the wireless variant of the vendor's LUNA33 model
# (model name from the driver's device table); its own product string is
# preferred when it has one, so a later revision keeps its marketing name.
MODEL_NAMES = {
    WIRELESS: "LunaFury Luna33-W",
    WIRED: "LunaFury Luna33",
}

MANUFACTURER = "lunafury"        # fallback match for models not in KNOWN_PIDS yet
USAGE_PAGE = 0xFFFF              # the vendor collection page (usage 0 on the receiver)
PREFERRED_INTERFACE = 2          # the collection that answered on the Luna33-W

REPLY_TRIES = 10                 # get the reply up to 10 times, 50 ms apart
ASLEEP_KEEP = 300                # s a silent mouse keeps its last, greyed-out level

Reading = Tuple[int, bool]


def vendor_collections(ifaces: List[dict]) -> List[dict]:
    """Every 0xFFFF collection, the one that answers first at the front.

    The receiver exposes two: interface 1 / usage 1 answers the request with a
    send error, interface 2 / usage 0 carries the level. Trying usage 0 and
    interface 2 first keeps the failing collection from costing a timeout.
    """
    vendor = [d for d in ifaces if d.get("usage_page") == USAGE_PAGE]
    vendor.sort(key=lambda d: (d.get("usage", 0) != 0,
                                d.get("interface_number") != PREFERRED_INTERFACE))
    return vendor


def _name_for(pid: int, ifaces: List[dict]) -> str:
    product = (ifaces[0].get("product_string") or "").strip()
    if product:
        return product if "lunafury" in product.lower() else f"LunaFury {product}"
    return MODEL_NAMES.get(pid, "LunaFury")


class LunafuryProvider(Provider):
    name = "lunafury"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def _read(self, path: bytes) -> Optional[Reading]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.send_feature_report([0x00] + QUERY + [0x00] * (64 - len(QUERY)))
            except (OSError, ValueError) as e:
                self._diag.append(f"    send: {e}")
                return None
            for _ in range(REPLY_TRIES):
                time.sleep(0.05)
                try:
                    resp = dev.get_feature_report(0, 65)
                except (OSError, ValueError):
                    resp = None
                batt, chg = parse_feature(resp)
                if batt is not None:
                    self._diag.append(f"    reply: {hexdump(resp, 12)}")
                    return batt, bool(chg)
            self._diag.append("    no a1 reply to request 0x83 (mouse asleep or off)")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _matches(self, d: dict) -> bool:
        if d.get("product_id") in KNOWN_PIDS:
            return True
        manufacturer = (d.get("manufacturer_string") or "").lower()
        return MANUFACTURER in manufacturer

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(LUNAFURY_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(lunafury): %s", e)
            return []

        groups: Dict[int, List[dict]] = {}
        for d in infos:
            if self._matches(d):
                groups.setdefault(d["product_id"], []).append(d)
        if not groups:
            return []

        # The name comes from the wireless id when both are present, so plugging
        # the cable in does not rename the icon: the cable is the same mouse.
        ordered = sorted(groups, key=lambda p: (p != WIRELESS, p != WIRED))
        name = _name_for(ordered[0], groups[ordered[0]])

        # The dongle and the cable are different pids, so the link is known for a
        # model in KNOWN_PIDS: dongle present -> 2.4g, cable only -> usb. A model
        # matched by manufacturer (unknown pid) honestly stays "" (inferred later).
        if WIRELESS in groups:
            transport = "2.4g"
        elif WIRED in groups:
            transport = "usb"
        else:
            transport = ""

        readings: List[Reading] = []
        # the cable first: it reports charging, and the dongle has nothing to add then
        for pid in sorted(groups, key=lambda p: p != WIRED):
            ifaces = groups[pid]
            self._diag.append(f"[LunaFury] pid={pid:04x} "
                              f"'{(ifaces[0].get('product_string') or '').strip()}'")
            for d in vendor_collections(ifaces):
                self._diag.append(f"  iface={d.get('interface_number')} usage="
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                res = self._read(d["path"])
                if res is not None:
                    readings.append(res)
                    break
            if readings and readings[0][1]:
                self._diag.append("  cable reports charging: skipping the rest")
                break

        key = "lunafury"
        if readings:
            batt, chg = next((r for r in readings if r[1]), readings[0])
            self._last = (batt, chg, time.time())
            return [DeviceStatus(key, name, batt, chg, True, "lunafury",
                                 kind="mouse", transport=transport)]
        if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
            return [DeviceStatus(key, name, self._last[0], self._last[1], False,
                                 "lunafury", kind="mouse", transport=transport)]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
