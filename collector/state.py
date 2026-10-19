"""StateStore: complete-snapshot gating, semantic diff, last_seen and gone.

Why this exists (BUILD_PLAN §4.2): the upstream merge helpers and the "device
disappeared" decision are *whole-poll* semantics. Pushing each provider's result
as soon as it arrives makes every device the other providers did not report in
that instant look gone. So:

* every enabled provider reports into a complete-snapshot gate; nothing is
  merged, diffed or emitted until the last one has reported;
* ``gone`` is decided only by the global 1 Hz tick, only from
  ``now - last_seen > max(2 * TTL, 10 s)`` - never from a "missing rounds" count;
* the three upstream merge helpers run only on the gated complete snapshot;
* ``device`` is emitted only on a semantic change (level/charging/online/name/
  approx/left_s/kind/transport/via), plus once more with ``replaces`` when the
  stable key itself changed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from . import protocol
from .merge import drop_bluetooth_duplicates, dedupe_controllers
from .protocol import make_device, semantic_equal
from .providers.base import DeviceStatus

log = logging.getLogger("halo_battery")

HEADSET_WORDS = ("blackshark", "kraken", "barracuda", "nari", "thresher", "headset",
                 "headphone", "earbud", "buds", "hammerhead", "airpods")
GAMEPAD_SOURCES = ("xinput", "playstation", "nintendo", "8bitdo", "steam")
KEYBOARD_WORDS = ("keychron", "lofree", "keyboard")

MIN_GONE_AGE = 10.0          # protocol.md §4.2: max(2 * TTL, 10 s)
RECENT_GONE_WINDOW = 60.0    # a key gone this recently may still be morphed


def normalize_kind(status: DeviceStatus) -> str:
    kind = (getattr(status, "kind", "") or "").lower()
    if kind in protocol.KINDS:
        return kind
    if kind in ("dualsense", "dualshock", "controller", "joystick", "joycon"):
        return "gamepad"
    name = (status.name or "").lower()
    source = (getattr(status, "source", "") or "").lower()
    if source in GAMEPAD_SOURCES:
        return "gamepad"
    if source == "bluetooth":
        return "bluetooth"
    if any(w in name for w in HEADSET_WORDS):
        return "headset"
    if any(w in name for w in KEYBOARD_WORDS) or source in ("keychron", "lofree"):
        return "keyboard"
    return "mouse"


def infer_transport(status: DeviceStatus, provider: str) -> str:
    """Best-effort transport for real providers (audit P2-7).

    The upstream ``DeviceStatus`` carries no transport field and providers are
    copied unmodified, so this uses what is actually reliable:

    * Bluetooth source / bt: key / ``via="bluetooth"`` -> bluetooth;
    * PlayStation: a MAC in the upstream key means Bluetooth, otherwise USB;
    * Steam: only the 2.4 GHz puck protocol is read -> 2.4g;
    * name/key words for 2.4 GHz receivers (LIGHTSPEED/HyperSpeed/receiver/...);
    * explicit USB/cable words;
    * ``unknown`` instead of a wrong guess (xinput cannot tell cable from the
      Xbox wireless adapter without WGI's IsWireless field).
    """
    source = (getattr(status, "source", "") or provider or "").lower()
    key = (status.key or "").lower()
    name = (status.name or "").lower()
    via = (getattr(status, "via", "") or "").lower()
    if source == "bluetooth" or key.startswith("bt:") or via == "bluetooth":
        return "bluetooth"
    if provider == "playstation" or source == "playstation":
        suffix = key.rsplit(":", 1)[-1] if ":" in key else ""
        if len(suffix) == 12 and all(c in "0123456789abcdef" for c in suffix):
            return "bluetooth"
        return "usb"
    if provider == "steam" or source == "steam":
        return "2.4g"
    if provider == "xinput" or source == "xinput":
        return "unknown"                 # cable vs Xbox wireless adapter: unknown
    if any(w in name or w in key for w in ("2.4", "lightspeed", "hyperspeed",
                                           "receiver", "dongle", "puck", "wi-fi")):
        return "2.4g"
    if "bluetooth" in name or " bt" in f" {name}":
        return "bluetooth"
    if any(w in name for w in ("usb", "wired", "cable")):
        return "usb"
    return "unknown"


class StateStore:
    """The only place that merges provider results and decides what to emit."""

    def __init__(self, providers: Iterable[str], sink: Any, keys: Any,
                 settings: Any,
                 ttl_for: Optional[Callable[[str, List[DeviceStatus]], float]] = None,
                 hints_for: Optional[Callable[[str, str], Optional[dict]]] = None,
                 history: Any = None, event_providers: Iterable[str] = (),
                 clock: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], float] = time.time,
                 bluetooth_provider: str = "bluetooth"):
        self._providers: List[str] = list(providers)
        self._gated: List[str] = [p for p in self._providers
                                  if p not in set(event_providers)]
        self._event_providers: Set[str] = set(event_providers)
        self._sink = sink
        self._keys = keys
        self._settings = settings
        self._history = history
        self._hints_for = hints_for
        self._clock = clock
        self._wall = wallclock
        self._bt_name = bluetooth_provider
        self._bluetooth_enabled = bluetooth_provider in self._providers
        self._ttl_for = ttl_for or (lambda provider, devices: settings.ttl_for_devices(
            provider, devices))

        self._lock = threading.RLock()
        self._snapshot: Dict[str, List[DeviceStatus]] = {}
        # First round: every enabled provider has to report. Later rounds are
        # started by the scheduler with the providers that are due; providers
        # that are not due keep their last complete result in the snapshot, so
        # their devices stay "seen" without being polled at the fast cadence.
        self._pending: Set[str] = set(self._gated)
        self._round_started = 0.0
        self._key_provider: Dict[str, str] = {}
        self._last_merged: List[DeviceStatus] = []
        self._ready = False                 # at least one complete snapshot merged

        self._devices: Dict[str, dict] = {}          # stable key -> Device
        self._last_seen: Dict[str, float] = {}       # stable key -> monotonic
        self._ttl: Dict[str, float] = {}
        self._provider_of: Dict[str, str] = {}
        self._in_flight: Set[str] = set()
        self._removed: Set[str] = set()
        # stable keys that were visible shortly before: a key change discovered a
        # little late may still morph the old key instead of delete+insert.
        self._recently_gone: Dict[str, float] = {}
        self._bt_snapshots = 0

        self._bt_logged: Set[str] = set()
        self._low_alerted: Set[str] = set()
        self._charging_seen: Set[str] = set()
        self._full_alerted: Set[str] = set()

        self._tick_requested = False
        self._last_tick = 0.0
        self._tick_interval = float(getattr(settings, "tick_interval", 30.0) or 30.0)
        self._gone_count = 0
        self._device_events = 0
        self._tick_events = 0

    # ------------------------------------------------------------ introspection
    @property
    def pending(self) -> Set[str]:
        with self._lock:
            return set(self._pending)

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    @property
    def coverage_complete(self) -> bool:
        """Every gated provider has reported at least once and a merge happened.

        The thread pool is smaller than the provider count, so a "complete" round
        may cover only the first N due providers; --once/--probe must not capture
        that partial first snapshot (it silently lost e.g. the Bluetooth device).
        """
        with self._lock:
            return self._ready and set(self._snapshot) >= set(self._gated)

    @property
    def bluetooth_snapshots(self) -> int:
        """How many complete Bluetooth watcher snapshots have arrived."""
        with self._lock:
            return self._bt_snapshots

    def snapshot_devices(self) -> List[dict]:
        with self._lock:
            return [dict(d) for d in self._devices.values()]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"device_events": self._device_events,
                    "tick_events": self._tick_events,
                    "gone": self._gone_count}

    def set_in_flight(self, providers: Iterable[str]) -> None:
        """Providers currently polling: their devices cannot be called gone while
        the provider is inside its blocking window (xinput's WGI helper can take
        up to 20 s; the plan's TTL >= 2 x block rule is enforced here as well)."""
        with self._lock:
            self._in_flight = set(providers)

    def set_ttl_for(self, ttl_for: Callable[[str, List[DeviceStatus]], float]) -> None:
        """Install the collector's TTL policy after both objects exist."""
        with self._lock:
            self._ttl_for = ttl_for
            self._ttl.clear()

    # ------------------------------------------------------------ provider input
    def begin_round(self, providers: Iterable[str]) -> None:
        """A new poll round: wait for these (due) providers before merging.

        The snapshot keeps the last complete result of providers that are not
        due this round - their devices are still the last known state, and the
        merge helpers need one *complete* set, not one per provider.
        """
        with self._lock:
            due = set(providers) & set(self._gated)
            if due:
                self._pending = due
                self._round_started = self._clock()

    def force_merge(self) -> None:
        """Round watchdog: merge what the snapshot has and move on."""
        with self._lock:
            if not self._pending:
                return
            log.warning("poll round stuck on %s; merging the last known snapshot",
                        ", ".join(sorted(self._pending)))
            self._pending = set()
            self._merge_and_apply()

    def on_provider_result(self, provider: str, devices: List[DeviceStatus]) -> bool:
        """One poll result. Returns True when this completed a full snapshot."""
        with self._lock:
            if provider not in self._gated:
                return False
            self._snapshot[provider] = list(devices or [])
            self._pending.discard(provider)
            if self._pending:
                return False
            self._merge_and_apply()
            self._ready = True
            return True

    def on_bluetooth_snapshot(self, devices: List[DeviceStatus]) -> bool:
        """An event-driven Bluetooth watcher snapshot (not gated).

        The watcher itself only prints complete snapshots, so its latest result
        is a complete Bluetooth set; when a HID snapshot is already on screen the
        merge re-runs immediately so a newly connected headset appears without
        waiting for the next vendor poll.
        """
        with self._lock:
            self._snapshot[self._bt_name] = list(devices or [])
            self._bt_snapshots += 1
            if not self._ready or self._pending:
                # Never merge into an open round: the snapshot would mix the new
                # Bluetooth set with half-updated provider results. The next
                # complete round picks it up.
                return False
            self._merge_and_apply()
            return True

    def _merge_and_apply(self) -> None:
        merged = self._merge(self._snapshot)
        self._last_merged = merged
        self._apply(merged)
        self._ready = True

    def _merge(self, snapshot: Dict[str, List[DeviceStatus]]) -> List[DeviceStatus]:
        hid: List[DeviceStatus] = []
        self._key_provider = {}
        for name in self._gated:
            if name == self._bt_name:
                continue
            for st in snapshot.get(name, []):
                self._key_provider[st.key] = name
                hid.append(st)
        bt = snapshot.get(self._bt_name, []) if self._bluetooth_enabled else []
        for st in bt:
            self._key_provider.setdefault(st.key, self._bt_name)
        if not self._bluetooth_enabled:
            return list(hid)
        results = dedupe_controllers(list(hid), list(bt)) + list(bt)
        return drop_bluetooth_duplicates(results, self._bt_logged)

    # ------------------------------------------------------------ apply
    def _apply(self, merged: List[DeviceStatus]) -> None:
        now_mono = self._clock()
        wall = int(self._wall())
        hidden = self._settings.hidden_keys()
        live_legacy = {st.key for st in merged}
        visible = set(self._devices) | set(hidden)     # hidden devices migrate too
        for key, gone_at in list(self._recently_gone.items()):
            if now_mono - gone_at <= RECENT_GONE_WINDOW:
                visible.add(key)
            else:
                del self._recently_gone[key]

        for st in merged:
            provider = self._provider_of_raw(st)
            hints = self._hints(provider, st.key)
            resolved = self._keys.resolve(provider, st.key, st, hints,
                                         now=float(wall), live_legacy=live_legacy,
                                         visible_stables=visible)
            key = resolved.stable
            replaces = resolved.replaces if resolved.replaces != key else None

            if replaces:
                old_last = self._last_seen.get(replaces)
                old_ttl = self._ttl.get(replaces)
                # user preferences follow the key (hidden devices must stay hidden,
                # a rename must not snap back to the hardware name; P2-6)
                self._settings.migrate_key(replaces, key)
                hidden = self._settings.hidden_keys()
                self._devices.pop(replaces, None)
                self._last_seen.pop(replaces, None)
                self._ttl.pop(replaces, None)
                self._provider_of.pop(replaces, None)
                self._low_alerted.discard(replaces)
                self._charging_seen.discard(replaces)
                self._full_alerted.discard(replaces)
                self._recently_gone.pop(replaces, None)
                # last_seen/TTL inheritance: the morphed device keeps its age, so a
                # late key-change detection cannot make it disappear immediately.
                if old_last is not None:
                    self._last_seen[key] = max(now_mono, old_last)
                if old_ttl is not None:
                    self._ttl[key] = max(self._ttl.get(key, old_ttl), old_ttl)

            self._recently_gone.pop(key, None)
            self._last_seen[key] = now_mono
            self._ttl[key] = float(self._ttl_for(provider, [st]))
            self._provider_of[key] = provider

            if key in hidden:
                # Hidden by the user: no device/tick row and no alert. A hidden
                # device whose key migrated still emits replaces? No - the UI has
                # no row to morph, and the preference moved with the key above.
                self._devices.pop(key, None)
                self._low_alerted.discard(key)
                self._charging_seen.discard(key)
                self._full_alerted.discard(key)
                continue

            name = self._settings.name_for(key) or st.name
            kind = (hints or {}).get("kind") or normalize_kind(st)
            transport = (hints or {}).get("transport") or infer_transport(st, provider)
            left_s = None
            if self._history is not None:
                try:
                    self._history.record(key, st.level, st.charging, st.online, wall,
                                         coarse=bool(st.approx))
                    left_s = self._history.seconds_left(key, st.level)
                except Exception:                 # history is advisory, never fatal
                    log.exception("history.record")
            device = make_device(
                key=key, name=name, level=st.level, approx=st.approx,
                left_s=left_s, charging=st.charging, online=st.online,
                kind=kind, transport=transport, via=st.via, updated_unix=wall)

            prev = self._devices.get(key)
            changed = bool(replaces) or not semantic_equal(prev, device)
            if not changed and prev is not None:
                # updated_unix means "last semantic change", not "last poll".
                device["updated_unix"] = prev.get("updated_unix", wall)
            self._devices[key] = device
            if changed:
                self._sink.device(dict(device), replaces)
                self._device_events += 1
                self._check_alerts(key, dict(device), prev, wall)

    def _provider_of_raw(self, st: DeviceStatus) -> str:
        provider = self._key_provider.get(st.key)
        if provider:
            return provider
        source = (getattr(st, "source", "") or "").lower()
        return source or "unknown"

    def _hints(self, provider: str, legacy_key: str) -> Optional[dict]:
        if self._hints_for is None:
            return None
        try:
            return self._hints_for(provider, legacy_key)
        except Exception:
            log.exception("hints_for(%s, %s)", provider, legacy_key)
            return None

    # ------------------------------------------------------------ alerts
    def _check_alerts(self, key: str, device: dict, prev: Optional[dict],
                      wall: int) -> None:
        low = int(self._settings.low)
        level = device.get("level")
        online = bool(device.get("online"))
        charging = bool(device.get("charging"))
        name = device.get("name") or "设备"

        # low battery: once per discharge, re-armed above low+5 %
        if level is None or not online:
            self._low_alerted.discard(key)
        elif charging or level > low + 5:
            self._low_alerted.discard(key)
        elif level <= low and key not in self._low_alerted:
            self._low_alerted.add(key)
            self._sink.alert("low", key, level, f"{name} 电量低（{level}%）")

        # full battery: only for a device that was seen charging *below* 100%.
        # A device first seen at 100 % + charging must stay silent (audit P3-9).
        if charging and level is not None and level < 100:
            self._charging_seen.add(key)
            self._full_alerted.discard(key)
        if level is not None and level >= 100 and key not in self._full_alerted \
                and key in self._charging_seen:
            if self._settings.get("full_alert", True):
                self._sink.alert("full", key, level, f"{name} 已充满")
            self._full_alerted.add(key)
            self._charging_seen.discard(key)

    # ------------------------------------------------------------ tick
    def on_tick(self) -> None:
        """The global 1 Hz tick: gone decisions, periodic full alignment, stats."""
        with self._lock:
            if not self._ready and not self._gated:
                # All providers disabled: there is nothing to gate on. Without
                # this --once would wait for a snapshot that can never come
                # (audit P3-7).
                self._ready = True
                self._emit_tick()
                return
            now = self._clock()
            self._expire(now)
            if self._tick_requested or now - self._last_tick >= self._tick_interval:
                self._emit_tick()

    def request_tick(self) -> None:
        with self._lock:
            self._tick_requested = True

    def refresh_from_settings(self) -> None:
        """A rename/hide arrived: re-apply the current complete snapshot and tick."""
        with self._lock:
            if self._ready:
                self._apply(self._last_merged)
            self._tick_requested = True

    def _expire(self, now: float) -> None:
        if self._pending:
            # A round is in progress: the truth for this instant is not known yet,
            # so no device may be called gone (a slow provider can take 20 s).
            return
        for key, last_seen in list(self._last_seen.items()):
            provider = self._provider_of.get(key, "")
            if provider in self._in_flight:
                continue                     # see set_in_flight()
            ttl = self._ttl.get(key)
            if ttl is None:
                ttl = float(self._settings.ttl_for(provider or "unknown"))
            if now - last_seen <= max(2.0 * ttl, MIN_GONE_AGE):
                continue
            was_visible = key in self._devices
            self._devices.pop(key, None)
            self._last_seen.pop(key, None)
            self._ttl.pop(key, None)
            self._provider_of.pop(key, None)
            self._low_alerted.discard(key)
            self._charging_seen.discard(key)
            self._full_alerted.discard(key)
            if was_visible:                 # hidden devices must not produce gone
                self._gone_count += 1
                self._recently_gone[key] = now
                self._sink.gone(key)

    def _emit_tick(self) -> None:
        self._tick_requested = False
        self._last_tick = self._clock()
        self._tick_events += 1
        self._sink.tick([dict(d) for d in self._devices.values()])

    # ------------------------------------------------------------ misc
    def settings_payload(self) -> Dict[str, Any]:
        poll = dict(self._settings.get("poll") or {})
        return {"poll": poll, "providers": list(self._providers)}

    def reset_alerts(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._low_alerted.clear()
                self._charging_seen.clear()
                self._full_alerted.clear()
            else:
                self._low_alerted.discard(key)
                self._charging_seen.discard(key)
                self._full_alerted.discard(key)
