"""Collector -> App NDJSON envelopes (protocol.md §1).

One place builds every message, so the field set, the ``seq`` counter and the
session id cannot drift between call sites. The writer only writes lines; it
does not know about devices.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .settings import build_string

PROTOCOL_VERSION = 1
# Exactly the list in protocol.md §1 (replaces is an envelope feature, not a
# capability flag).
CAPABILITIES = ["approx", "left_s", "via", "transport"]

KINDS = ("headset", "mouse", "keyboard", "gamepad", "bluetooth")
TRANSPORTS = ("2.4g", "usb", "bluetooth", "unknown")
ALERT_KINDS = ("low", "full", "offline")

UNKNOWN_NAME = "未知设备"


def _normalize_level(level: Any) -> Optional[int]:
    """int 0..100 or None. ``bool`` is not a level; float is rounded."""
    if level is None or isinstance(level, bool):
        return None
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return None
    return max(0, min(100, int(round(level))))


def make_device(key: str, name: str, level: Optional[int] = None,
                approx: str = "", left_s: Optional[float] = None,
                charging: bool = False, online: bool = True,
                kind: str = "", transport: str = "unknown", via: str = "",
                updated_unix: Optional[int] = None) -> dict:
    """The exact Device object of protocol.md §1. Never omit a field."""
    if kind not in KINDS:
        kind = "bluetooth" if transport == "bluetooth" else "mouse"
    if transport not in TRANSPORTS:
        transport = "unknown"
    left = None if left_s is None else max(0, int(round(left_s)))
    return {
        "key": str(key),
        "name": str(name or "").strip() or UNKNOWN_NAME,
        "level": _normalize_level(level),
        "approx": str(approx or ""),
        "left_s": left,
        "charging": bool(charging),
        "online": bool(online),
        "kind": kind,
        "transport": transport,
        "via": str(via or ""),
        "updated_unix": int(updated_unix if updated_unix is not None else time.time()),
    }


# Fields whose change is a semantic change ("only send device on a semantic
# change", protocol.md §1). updated_unix is deliberately not in this set.
SEMANTIC_FIELDS = ("name", "level", "approx", "left_s", "charging", "online",
                   "kind", "transport", "via")


def semantic_equal(a: Optional[dict], b: Optional[dict]) -> bool:
    if a is None or b is None:
        return a is b
    return all(a.get(f) == b.get(f) for f in SEMANTIC_FIELDS)


class Protocol:
    """Builds and sends the envelopes; ``seq`` is monotonic per session."""

    def __init__(self, writer: Any, session: Optional[str] = None,
                 build: Optional[str] = None):
        self.writer = writer
        self.session = session or str(uuid.uuid4())
        self.build = build or build_string()
        self._seq = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ internals
    @property
    def seq(self) -> int:
        return self._seq

    def _emit(self, message: Dict[str, Any], counted: bool = True) -> Dict[str, Any]:
        with self._lock:
            if counted:
                self._seq += 1
                message["seq"] = self._seq
            self.writer.send(message)
        return message

    def new_session(self) -> str:
        """Suspend/resume or a sidecar restart: a fresh session id and seq."""
        with self._lock:
            self.session = str(uuid.uuid4())
            self._seq = 0
            return self.session

    def reset_seq(self) -> None:
        """Start counting from 1 again (used after a buffered --once collection)."""
        with self._lock:
            self._seq = 0

    # ------------------------------------------------------------ events
    def hello(self, devices: List[dict],
              settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        msg = {
            "t": "hello",
            "protocol": PROTOCOL_VERSION,
            "seq": 0,                      # protocol.md: hello's seq is fixed 0
            "session": self.session,
            "build": self.build,
            "capabilities": list(CAPABILITIES),
            "devices": devices,
            "settings": settings or {"poll": {}, "providers": []},
        }
        return self._emit(msg, counted=False)

    def device(self, device: dict, replaces: Optional[str] = None) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"t": "device", "session": self.session,
                               "device": device}
        if replaces and replaces != device.get("key"):
            msg["replaces"] = replaces
        return self._emit(msg)

    def gone(self, key: str) -> Dict[str, Any]:
        return self._emit({"t": "gone", "session": self.session, "key": key})

    def tick(self, devices: List[dict]) -> Dict[str, Any]:
        return self._emit({"t": "tick", "session": self.session,
                           "devices": devices})

    def alert(self, kind: str, key: str, level: Optional[int] = None,
              text: str = "") -> Dict[str, Any]:
        if kind not in ALERT_KINDS:
            raise ValueError(f"alert kind must be one of {ALERT_KINDS}")
        return self._emit({"t": "alert", "session": self.session, "kind": kind,
                           "key": key, "level": _normalize_level(level),
                           "text": text})

    def stat(self, providers: Dict[str, Dict[str, Any]], timers_per_s: float,
             key_migrations: int = 0) -> Dict[str, Any]:
        return self._emit({"t": "stat", "session": self.session,
                           "providers": providers,
                           "timers_per_s": round(float(timers_per_s), 3),
                           "key_migrations": int(key_migrations)})


class RecordingSink:
    """A Protocol-compatible sink for unit tests: keeps every message."""

    def __init__(self):
        self.messages: List[Dict[str, Any]] = []
        self.session = "test-session"

    def _add(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        self.messages.append(msg)
        return msg

    def hello(self, devices, settings=None):
        return self._add({"t": "hello", "protocol": PROTOCOL_VERSION, "seq": 0,
                          "session": self.session, "devices": list(devices),
                          "settings": settings or {}})

    def device(self, device, replaces=None):
        msg = {"t": "device", "session": self.session, "device": device}
        if replaces:
            msg["replaces"] = replaces
        return self._add(msg)

    def gone(self, key):
        return self._add({"t": "gone", "session": self.session, "key": key})

    def tick(self, devices):
        return self._add({"t": "tick", "session": self.session,
                          "devices": list(devices)})

    def alert(self, kind, key, level=None, text=""):
        return self._add({"t": "alert", "session": self.session, "kind": kind,
                          "key": key, "level": level, "text": text})

    def stat(self, providers, timers_per_s, key_migrations=0):
        return self._add({"t": "stat", "session": self.session,
                          "providers": providers,
                          "timers_per_s": timers_per_s,
                          "key_migrations": key_migrations})

    # helpers for tests
    def by_type(self, t: str) -> List[Dict[str, Any]]:
        return [m for m in self.messages if m.get("t") == t]

    def last(self, t: str) -> Optional[Dict[str, Any]]:
        found = self.by_type(t)
        return found[-1] if found else None

    def clear(self) -> None:
        self.messages.clear()
