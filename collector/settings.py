"""Collector settings and file locations.

Everything lives under ``%LOCALAPPDATA%\\PeripheralBattery`` (see BUILD_PLAN §6.3):

    config.json     collector settings (poll cadence, thresholds, renames/hidden)
    aliases.json    stable device key mapping + migration history
    history.json    battery drain model (copied from the upstream app)
    logs/collector.log   rotating log written by the stderr thread

``PB_DATA_DIR`` overrides the directory (tests and the demo use a temp folder);
``PB_BUILD`` overrides the build string that goes into ``hello``.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Dict, Iterable, List, Optional

APP_NAME = "PeripheralBattery"
CONFIG_NAME = "config.json"
ALIASES_NAME = "aliases.json"
HISTORY_NAME = "history.json"
LOG_DIR_NAME = "logs"
LOG_NAME = "collector.log"
DIAG_DIR_NAME = "diagnostics"

# BUILD_PLAN §3.3. Keys are provider names, values are the poll cadence in seconds.
# TTLs are derived from the cadence below (see ttl_for); the invariant is
# TTL >= 2 x the provider's worst blocking window.
DEFAULT_POLL = {
    "mouse": 5,
    "keyboard": 15,
    "headset": 15,
    "gamepad": 10,
    "steam": 30,
    "bluetooth": 10,
}

# Which cadence category each upstream provider belongs to. A provider that can
# return several kinds gets the fastest category it may produce (a Razer poll
# reads mice and headsets, so it is polled like a mouse).
PROVIDER_CATEGORY = {
    "am_infinity": "mouse",
    "astro": "headset",
    "asus": "mouse",
    "audeze": "headset",
    "barracuda": "headset",
    "bluetooth": "bluetooth",
    "corsair": "headset",
    "8bitdo": "gamepad",
    "gwolves": "mouse",
    "hyperx": "headset",
    "hyperx_alpha2": "headset",
    "hyperx_cloud3": "headset",
    "hyperx_cloud3s": "headset",
    "jbl": "headset",
    "keychron": "keyboard",
    "lamzu": "mouse",
    "lofree": "keyboard",
    "logitech": "mouse",
    "logitech_centurion": "headset",
    "lunafury": "mouse",
    "mchose": "mouse",
    "nintendo": "gamepad",
    "nzxt": "mouse",
    "playstation": "gamepad",
    "pulsar": "mouse",
    "razer": "mouse",
    "steam": "steam",
    "steelseries": "mouse",
    "steelseries_elite": "headset",
    "wlmouse": "mouse",
    "xinput": "gamepad",
}

# Provider names in the order they are polled (upstream make_providers order,
# Bluetooth last because it is merged from the watcher cache).
PROVIDER_ORDER = (
    "razer", "audeze", "wlmouse", "mchose", "hyperx_alpha2", "hyperx_cloud3",
    "hyperx", "keychron", "pulsar", "jbl", "logitech", "steelseries", "xinput",
    "playstation", "8bitdo", "barracuda", "nintendo", "asus", "gwolves",
    "lofree", "astro", "corsair", "lamzu", "lunafury", "am_infinity",
    "steelseries_elite",
    "logitech_centurion", "hyperx_cloud3s", "nzxt", "steam", "bluetooth",
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "poll": dict(DEFAULT_POLL),
    "providers": None,        # None = every provider; a list = only those
    "bluetooth": True,
    "low": 20,                # low battery threshold, %
    "full_alert": True,       # alert when a charging device reaches 100 %
    "tick_interval": 30.0,    # s between full tick events
    "stat_interval": 60.0,    # s between stat events
    "tick_hz": 1.0,           # scheduler wake-ups per second (P8: <= 1)
    "threads": 4,             # provider poll pool size
    "wake_on_input": True,    # re-poll greyed devices when the user comes back
    # Bluetooth connection poll inside the upstream PowerShell watcher, in ms.
    # 30 s (was 2 s upstream) is the product trade-off accepted for P2 headroom:
    # battery snapshots still happen on connect (+3/+8/+15 s) and once a minute,
    # only the "did a device connect/disconnect" check is delayed by up to this
    # interval. PB_BT_WATCH_MS overrides it at runtime; 2000 ms is the floor so a
    # typo cannot restore the old busy-poll cost accidentally.
    "bt_watch_ms": 30000,
    "names": {},              # stable key -> user rename
    "hidden": [],             # stable keys the user hid
}

LIMITS = {"low": (0, 100), "tick_interval": (1.0, 3600.0),
          "stat_interval": (5.0, 3600.0), "tick_hz": (0.2, 1.0), "threads": (1, 16),
          "bt_watch_ms": (2000, 300000)}

# Fields the ``set`` command may rewrite without a device key (F-04).  The
# settings drawer sends ``{"t":"set","field":"low","value":25}``; these values
# are global, so the ``key`` argument is irrelevant for them.
GLOBAL_SET_FIELDS = frozenset({"low", "full_alert"})


def _env_data_dir() -> str:
    override = os.environ.get("PB_DATA_DIR")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_NAME)


def _valid(key: str, value: Any) -> bool:
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and LIMITS.get(key, (float("-inf"), float("inf")))[0]
                <= float(value) <= LIMITS.get(key, (value, value))[1])
    if isinstance(default, int):
        return (isinstance(value, int) and not isinstance(value, bool)
                and LIMITS.get(key, (value, value))[0] <= value
                <= LIMITS.get(key, (value, value))[1])
    if key == "providers":
        return value is None or (isinstance(value, list)
                                 and all(isinstance(v, str) for v in value))
    if key == "poll":
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, (int, float))
            and not isinstance(v, bool) and v >= 1 for k, v in value.items())
    if key == "names":
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    if key == "hidden":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, type(default))


def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Settings:
    """config.json with the defaults above, safe to read from several threads."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = os.path.abspath(data_dir) if data_dir else _env_data_dir()
        self.path = os.path.join(self.data_dir, CONFIG_NAME)
        self._lock = threading.RLock()
        self._values: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.load()

    # ------------------------------------------------------------ files
    @property
    def aliases_path(self) -> str:
        return os.path.join(self.data_dir, ALIASES_NAME)

    @property
    def history_path(self) -> str:
        return os.path.join(self.data_dir, HISTORY_NAME)

    @property
    def log_path(self) -> str:
        return os.path.join(self.data_dir, LOG_DIR_NAME, LOG_NAME)

    @property
    def diagnostics_dir(self) -> str:
        return os.path.join(self.data_dir, DIAG_DIR_NAME)

    def ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        os.makedirs(self.diagnostics_dir, exist_ok=True)

    def load(self) -> None:
        """Read config.json; a damaged file falls back to the defaults per key."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            for key, value in data.items():
                if key in DEFAULT_SETTINGS and _valid(key, value):
                    self._values[key] = value

    def save(self) -> bool:
        with self._lock:
            data = json.loads(json.dumps(self._values))
        try:
            _atomic_write(self.path, data)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------ values
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        if key not in DEFAULT_SETTINGS or not _valid(key, value):
            return False
        with self._lock:
            self._values[key] = value
        return True

    # ------------------------------------------------------------ convenience
    @property
    def tick_interval(self) -> float:
        return float(self.get("tick_interval", 30.0))

    @property
    def stat_interval(self) -> float:
        return float(self.get("stat_interval", 60.0))

    @property
    def tick_hz(self) -> float:
        return float(self.get("tick_hz", 1.0))

    @property
    def low(self) -> int:
        return int(self.get("low", 20))

    def poll_seconds(self, category: str) -> float:
        poll = self.get("poll") or DEFAULT_POLL
        return float(poll.get(category, DEFAULT_POLL.get(category, 15)))

    def categories(self, provider: str) -> Iterable[str]:
        return (PROVIDER_CATEGORY.get(provider, "headset"),)

    def provider_interval(self, provider: str) -> float:
        """Poll cadence for a provider, in seconds (Steam: 8 s while present)."""
        intervals = [self.poll_seconds(c) for c in self.categories(provider)]
        return max(min(intervals) if intervals else 15.0, 1.0)

    def ttl_for(self, provider: str) -> float:
        """How long a device may stay unseen before the global tick calls it gone.

        BUILD_PLAN §3.3: TTL >= 2 x the provider's worst blocking window. The
        cadences in DEFAULT_POLL already carry that margin; the tick uses
        max(2 * TTL, 10 s) as the actual deadline (§4.2).
        """
        if provider == "steam":
            return 30.0
        if provider == "bluetooth":
            return 15.0
        return self.provider_interval(provider)

    def ttl_for_devices(self, provider: str, devices: Iterable[Any]) -> float:
        """Per provider, but Steam's TTL drops to 8 s while a controller is present."""
        if provider == "steam" and any(getattr(d, "online", True) for d in devices):
            return 8.0
        return self.ttl_for(provider)

    def is_enabled(self, provider: str) -> bool:
        if provider == "bluetooth":
            return bool(self.get("bluetooth", True))
        selected = self.get("providers")
        return selected is None or provider in selected

    # ------------------------------------------------------------ user edits
    def set_name(self, key: str, name: str) -> None:
        with self._lock:
            names = dict(self._values.get("names") or {})
            if name:
                names[key] = name
            else:
                names.pop(key, None)
            self._values["names"] = names

    def name_for(self, key: str) -> str:
        with self._lock:
            return (self._values.get("names") or {}).get(key, "")

    def set_hidden(self, key: str, hidden: bool) -> None:
        with self._lock:
            keys: List[str] = list(self._values.get("hidden") or [])
            if hidden and key not in keys:
                keys.append(key)
            elif not hidden:
                keys = [k for k in keys if k != key]
            self._values["hidden"] = keys

    def hidden_keys(self) -> frozenset:
        with self._lock:
            return frozenset(self._values.get("hidden") or ())

    def apply_set(self, key: str, field: str, value: Any) -> bool:
        """The protocol's ``{"t":"set"}`` command. Persists immediately.

        Global fields (``GLOBAL_SET_FIELDS``) ignore *key* so the UI can send
        the command without one; per-device fields still need a stable key.
        """
        if isinstance(field, str) and field in GLOBAL_SET_FIELDS:
            if not self.set(field, value):
                return False
        elif field == "name":
            if not isinstance(value, str):
                return False
            self.set_name(key, value)
        elif field == "hidden":
            if not isinstance(value, bool):
                return False
            self.set_hidden(key, value)
        else:
            return False
        self.save()
        return True

    def migrate_key(self, old: str, new: str) -> bool:
        """Carry the user's rename/hidden choice to a migrated stable key (P2-6)."""
        if not old or not new or old == new:
            return False
        changed = False
        with self._lock:
            names = dict(self._values.get("names") or {})
            if old in names:
                names[new] = names.pop(old)
                self._values["names"] = names
                changed = True
            hidden = list(self._values.get("hidden") or [])
            if old in hidden:
                hidden = [k for k in hidden if k != old]
                if new not in hidden:
                    hidden.append(new)
                self._values["hidden"] = hidden
                changed = True
        if changed:
            self.save()
        return changed


def build_string() -> str:
    """Best effort build id for hello; protocol.json shows it for diagnostics only."""
    override = os.environ.get("PB_BUILD")
    if override:
        return override
    return "unknown"
