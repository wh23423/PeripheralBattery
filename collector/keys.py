"""Stable device keys and the persisted alias table (protocol.md §3, P9).

Upstream keys cannot be used as device identity: ``xinput:{slot}`` reuses slots,
``ps:{pid}:{mac-or-usb}`` changes when a cable is added, ``logitech:...`` switches
from the receiver slot to the unit id once that id has been read, and several
providers key devices by product id alone. This module rebuilds the key as

    provider ":" vid ":" pid ":" (serial | mac | unit-<id> | "u" + ordinal)

Priority is serial > mac > unit id > persisted ordinal, exactly as the protocol
requires. Nothing in the key comes from a slot, an index, an enumeration order
or a process-local counter:

* hardware ids are parsed out of the upstream key (``bt:<mac>``, ``ps:<pid>:<mac>``,
  ``logitech:<unit>`` ...) or supplied as hints by a provider adapter;
* the ordinal is allocated once per ``(provider, vid, pid, name)`` and written to
  ``aliases.json``; deleting a device never recycles its ordinal.

``aliases.json`` also keeps the migration history P9 is measured with, and the
upstream-key -> stable-key table that survives restarts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("halo_battery")

ALIASES_VERSION = 1
STALE_AFTER = 30.0          # s: an alias not seen this long may be re-pointed
SAVE_EVERY = 30.0           # s between background writes of aliases.json
HISTORY_LIMIT = 200         # migration records kept
_ORDINAL_RE = re.compile(r":u(\d+)$")

# Vendor ids of the upstream providers, taken from their source constants. Used
# only when the upstream key names the product id but not the vendor id; a hint
# always wins.
PROVIDER_VID = {
    "am_infinity": "3151",
    "astro": "046d",
    "asus": "0b05",
    "audeze": "3329",
    "barracuda": "1532",
    "bluetooth": "0000",
    "corsair": "1b1c",
    "8bitdo": "2dc8",
    "gwolves": "33e4",
    "hyperx": "03f0",
    "hyperx_alpha2": "03f0",
    "hyperx_cloud3": "03f0",
    "hyperx_cloud3s": "03f0",
    "jbl": "0ecb",
    "keychron": "3434",
    "lamzu": "373e",
    "lofree": "388d",
    "logitech": "046d",
    "logitech_centurion": "046d",
    "mchose": "5253",
    "nintendo": "057e",
    "nzxt": "1e71",
    "playstation": "054c",
    "pulsar": "0000",
    "razer": "1532",
    "steam": "28de",
    "steelseries": "1038",
    "steelseries_elite": "1038",
    "wlmouse": "0000",
    "xinput": "0000",
}

_SEP_RE = re.compile(r"[\s\-_.:/\\{}\[\]]+")
_HEX4_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{4})(?![0-9a-f])")
_HEX8_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{8})(?![0-9a-f])")
_HEX12_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{12})(?![0-9a-f])")


def _norm(value: Any) -> str:
    """Lower case, separators removed: how the protocol compares serials."""
    if value is None:
        return ""
    return _SEP_RE.sub("", str(value).strip().lower())


_KEY_SAFE_RE = re.compile(r"[^a-z0-9]+")


def _key_token(value: Any) -> str:
    """A stable-key suffix safe for the NDJSON contract ([a-z0-9] only).

    PlayStation USB topology ids look like ``usb7&11aa&0&0000``; ``&`` would make
    the key fail tools/collector_ndjson_check.py and is not a documented key
    character (audit P3-4).
    """
    return _KEY_SAFE_RE.sub("", str(value or "").lower())[:64]


def _fingerprint_name(provider: str, name: Any) -> str:
    """Name part of the identity fingerprint.

    xinput appends " 1"/" 2" to controllers when more than one is connected and
    Steam names its slots the same way; that number follows the connection order,
    not the device, so it must not split the fingerprint (audit P1-2).
    """
    words = [w for w in re.split(r"[^a-z0-9]+", str(name or "").lower()) if w]
    if provider in ("xinput", "steam") and len(words) > 1 and words[-1].isdigit():
        words.pop()
    return "".join(words)


def _hex4(value: Any) -> str:
    """A 4-digit lower-case hex id or '' if it is not one."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return "%04x" % (value & 0xFFFF)
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    try:
        return "%04x" % (int(text, 16) & 0xFFFF)
    except ValueError:
        return ""


def _name_key(name: Any) -> str:
    """Normalised device name used to pair an old key with a new one.

    "G502 X PLUS", "g502 x plus" and "G502-X-PLUS" are the same device; the
    transport words are kept here (unlike merge.device_family) because a key
    change never changes the device's own name.
    """
    words = "".join(c if c.isalnum() else " " for c in (name or "").lower()).split()
    return " ".join(words)


def _compatible_id(a: str, b: str) -> bool:
    """Hardware ids match, unless one side is the unknown placeholder 0000."""
    return a == b or a == "0000" or b == "0000"


def _mac(value: Any) -> str:
    """A 12-hex MAC (separators allowed) or ''."""
    text = _norm(value)
    return text if len(text) == 12 and all(c in "0123456789abcdef" for c in text) else ""


@dataclass(frozen=True)
class Identity:
    """What is known about a device independent of the upstream key."""
    provider: str
    vid: str = "0000"
    pid: str = "0000"
    serial: str = ""
    mac: str = ""
    unit: str = ""
    name: str = ""
    kind: str = ""
    transport: str = ""
    hint: Dict[str, Any] = field(default_factory=dict, compare=False)

    def suffix(self) -> Optional[str]:
        """The best stable suffix, or None when only an ordinal is possible."""
        serial = _key_token(self.serial)
        if serial:
            return serial
        mac = _key_token(self.mac)
        if mac:
            return mac
        unit = _key_token(self.unit)
        if unit:
            return "unit-" + unit
        return None

    def hw_key(self) -> Optional[str]:
        suffix = self.suffix()
        if suffix is None:
            return None
        return f"{self.provider}:{self.vid}:{self.pid}:{suffix}"

    def fingerprint(self) -> str:
        """Identity used to reuse an ordinal when the upstream key shape changes."""
        return "|".join((self.provider, self.vid, self.pid,
                         _fingerprint_name(self.provider, self.name)))

    def with_defaults(self, other: "Identity") -> "Identity":
        """Fill ids this key does not carry (logitech unit keys lose the pid)."""
        return replace(
            self,
            vid=self.vid if self.vid != "0000" else other.vid,
            pid=self.pid if self.pid != "0000" else other.pid,
            name=self.name or other.name,
            kind=self.kind or other.kind,
            transport=self.transport or other.transport,
        )


@dataclass
class Resolved:
    stable: str
    replaces: Optional[str] = None
    identity: Optional[Identity] = None
    is_new: bool = False


def parse_legacy(provider: str, legacy_key: str) -> Identity:
    """Best effort hardware identity from an upstream key (no slots as identity)."""
    low = (legacy_key or "").strip().lower()
    body = low.split(":", 1)[1] if ":" in low else low
    tokens = [t for t in body.split(":") if t]
    vid = PROVIDER_VID.get(provider, "0000")
    pid = "0000"
    serial = mac = unit = ""

    if provider == "bluetooth":
        found = _HEX12_RE.search(low)
        mac = found.group(1) if found else ""
        return Identity(provider, vid, pid, mac=mac)

    if provider == "logitech":
        # "logitech:<unit>" or "logitech:<receiver-instance>:<unit>": both end in
        # an 8-hex unit id. Without one, drop to "logitech:<pid>:<slot>".
        if tokens and re.fullmatch(r"[0-9a-f]{8}", tokens[-1]):
            unit = tokens[-1]
            for token in tokens[:-1]:
                if re.fullmatch(r"[0-9a-f]{4}", token):
                    pid = token
                    break
        else:
            hexes = [t for t in tokens if re.fullmatch(r"[0-9a-f]{4}", t)]
            if hexes:
                pid = hexes[0]
        return Identity(provider, vid, pid, unit=unit)

    if provider == "playstation":
        vid = "054c"
        if tokens:
            pid = _hex4(tokens[0]) or "0000"
        ident = _norm(":".join(tokens[1:])) if len(tokens) > 1 else ""
        if ident:
            if len(ident) == 12 and all(c in "0123456789abcdef" for c in ident):
                mac = ident
            else:
                serial = ident
        return Identity(provider, vid, pid, serial=serial, mac=mac)

    if provider == "nintendo":
        vid = "057e"
        if tokens:
            pid = _hex4(tokens[0]) or "0000"
        found = _HEX12_RE.search(low)
        mac = found.group(1) if found else ""
        return Identity(provider, vid, pid, mac=mac)

    if provider == "8bitdo":
        vid = "2dc8"
        if tokens:
            pid = _hex4(tokens[0]) or "0000"
        if len(tokens) > 1:
            serial = _norm(tokens[1])
        return Identity(provider, vid, pid, serial=serial)

    if provider == "pulsar":
        # upstream key: f"pulsar:{vid:04x}{pid:04x}"
        if tokens and re.fullmatch(r"[0-9a-f]{8}", tokens[0]):
            vid, pid = tokens[0][:4], tokens[0][4:]
        return Identity(provider, vid, pid)

    if provider == "steam":
        if tokens:
            pid = _hex4(tokens[0]) or "0000"
        return Identity(provider, vid, pid)     # the rest is a slot: not identity

    # razer: f"razer:{pid:04x}:{serial}" - a trailing serial is identity.
    if provider == "razer":
        if tokens:
            pid = _hex4(tokens[0]) or "0000"
        if len(tokens) > 1:
            serial = _norm(tokens[1])
        return Identity(provider, vid, pid, serial=serial)

    # everything else: a product id, sometimes a MAC, sometimes "{vid}{pid}".
    if tokens and re.fullmatch(r"[0-9a-f]{8}", tokens[0]) and provider in (
            "pulsar",):
        vid, pid = tokens[0][:4], tokens[0][4:]
    else:
        hexes = [t for t in tokens if re.fullmatch(r"[0-9a-f]{4}", t)]
        if len(hexes) >= 2:
            vid, pid = hexes[0], hexes[1]
        elif len(hexes) == 1:
            pid = hexes[0]
    found = _HEX12_RE.search(low)
    if found:
        mac = found.group(1)
    return Identity(provider, vid, pid, serial=serial, mac=mac)


def build_identity(provider: str, legacy_key: str,
                   hints: Optional[Dict[str, Any]] = None) -> Identity:
    """Parse the upstream key, then let adapter hints override what they know."""
    parsed = parse_legacy(provider, legacy_key)
    if not hints:
        return parsed
    hint_vid = _hex4(hints.get("vid"))
    hint_pid = _hex4(hints.get("pid"))
    hint_serial = _norm(hints.get("serial"))
    hint_mac = _mac(hints.get("mac"))
    hint_unit = _norm(hints.get("unit_id", hints.get("unit")))
    # A hint that names a MAC may arrive as "mac"; a hint may also give a raw
    # serial that happens to be a MAC (Windows exposes Bluetooth ids that way).
    if not hint_mac and hint_serial and len(hint_serial) == 12 \
            and all(c in "0123456789abcdef" for c in hint_serial):
        hint_mac, hint_serial = hint_serial, ""
    # If the adapter supplied any hardware id, it is authoritative: a bogus id
    # parsed out of the upstream key must not shadow it.
    hint_has_id = bool(hint_serial or hint_mac or hint_unit)
    return Identity(
        provider=provider,
        vid=hint_vid or parsed.vid,
        pid=hint_pid or parsed.pid,
        serial=hint_serial if hint_has_id else parsed.serial,
        mac=hint_mac if hint_has_id else parsed.mac,
        unit=hint_unit if hint_has_id else parsed.unit,
        name=str(hints.get("name") or parsed.name or ""),
        kind=str(hints.get("kind") or parsed.kind or ""),
        transport=str(hints.get("transport") or parsed.transport or ""),
        hint=dict(hints),
    )


class KeyStore:
    """aliases.json: upstream key -> stable key, ordinal allocation, migrations."""

    def __init__(self, path: Optional[str], clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._aliases: Dict[str, Dict[str, Any]] = {}
        self._owners: Dict[str, str] = {}
        self._next_ordinal: Dict[str, int] = {}
        self._history: List[Dict[str, Any]] = []
        self._migrations = 0
        self._last_save = 0.0
        self._dirty = False
        self.load()

    # ------------------------------------------------------------ storage
    def load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            aliases = data.get("aliases")
            if isinstance(aliases, dict):
                for legacy, entry in aliases.items():
                    if not isinstance(legacy, str) or not isinstance(entry, dict):
                        continue
                    stable = entry.get("stable")
                    if not isinstance(stable, str) or not stable:
                        continue
                    self._aliases[legacy] = {
                        "stable": stable,
                        "provider": str(entry.get("provider", "")),
                        "vid": _hex4(entry.get("vid")) or "0000",
                        "pid": _hex4(entry.get("pid")) or "0000",
                        "name": str(entry.get("name", "")),
                        "first_seen": float(entry.get("first_seen", 0.0)),
                        "last_seen": float(entry.get("last_seen", 0.0)),
                    }
                    self._owners.setdefault(stable, legacy)
            next_ordinal = data.get("next_ordinal")
            if isinstance(next_ordinal, dict):
                for scope, n in next_ordinal.items():
                    if isinstance(scope, str) and isinstance(n, int) and n >= 0:
                        self._next_ordinal[scope] = n
            history = data.get("history")
            if isinstance(history, list):
                self._history = [h for h in history if isinstance(h, dict)][-HISTORY_LIMIT:]
            migrations = data.get("migrations")
            self._migrations = migrations if isinstance(migrations, int) else len(self._history)

    def save(self, force: bool = False) -> bool:
        if not self.path:
            return False
        with self._lock:
            if not self._dirty and not force:
                return True
            data = {
                "version": ALIASES_VERSION,
                "next_ordinal": dict(self._next_ordinal),
                "aliases": {k: dict(v) for k, v in self._aliases.items()},
                "history": list(self._history),
                "migrations": int(self._migrations),
            }
            self._dirty = False
            self._last_save = self._clock()
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".aliases-", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError:
            log.exception("could not write %s", self.path)
            return False

    def _maybe_save(self, now: float) -> None:
        if self._dirty and now - self._last_save >= SAVE_EVERY:
            self.save()

    # ------------------------------------------------------------ read-only
    @property
    def migration_count(self) -> int:
        with self._lock:
            return int(self._migrations)

    def history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._history]

    def aliases(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._aliases.items()}

    def stable_for(self, legacy_key: str) -> Optional[str]:
        with self._lock:
            entry = self._aliases.get(legacy_key)
            return entry["stable"] if entry else None

    # ------------------------------------------------------------ resolve
    def resolve(self, provider: str, legacy_key: str, status: Any = None,
                hints: Optional[Dict[str, Any]] = None,
                now: Optional[float] = None,
                live_legacy: Optional[set] = None,
                visible_stables: Optional[set] = None) -> Resolved:
        """Map one upstream key to its stable key, migrating/allocating as needed.

        ``live_legacy`` is the set of upstream keys present in this complete
        snapshot: it tells a key change (old key gone, new key appears) apart
        from a second identical device (both keys present). ``visible_stables``
        is the set of stable keys currently shown; a new upstream key may only
        take over an old *visible* ordinal key when exactly one candidate
        matches (same provider, compatible vid/pid, same normalised name) - two
        identical devices produce two candidates and therefore no replaces.
        """
        with self._lock:
            now = self._clock() if now is None else now
            live_known = live_legacy is not None
            live = set(live_legacy or ())
            visible = None if visible_stables is None else set(visible_stables)
            identity = build_identity(provider, legacy_key, hints)
            name = identity.name or str(getattr(status, "name", "") or "")
            if name and not identity.name:
                identity = replace(identity, name=name)

            alias = self._aliases.get(legacy_key)
            if alias is not None:
                return self._resolve_known(legacy_key, alias, identity, now)
            return self._resolve_new(legacy_key, identity, now, live, visible,
                                     live_known)

    def _resolve_known(self, legacy_key: str, alias: Dict[str, Any],
                       identity: Identity, now: float) -> Resolved:
        stable = alias["stable"]
        # A logitech receiver slot becomes a unit id once the device answers the
        # information feature: upgrade the key and let the UI morph (replaces).
        inherited = Identity(provider=identity.provider, vid=alias.get("vid", "0000"),
                             pid=alias.get("pid", "0000"),
                             name=alias.get("name", ""))
        identity = identity.with_defaults(inherited)
        candidate = identity.hw_key()
        if candidate and candidate != stable and _ORDINAL_RE.search(stable):
            self._migrate(legacy_key, alias, stable, candidate, identity, now,
                          reason="identity-upgraded")
            return Resolved(candidate, stable, identity, False)
        self._touch(alias, identity, now)
        return Resolved(stable, None, identity, False)

    def _resolve_new(self, legacy_key: str, identity: Identity,
                     now: float, live: set,
                     visible: Optional[set] = None,
                     live_known: bool = False) -> Resolved:
        candidate = identity.hw_key()
        if candidate:
            owner_key = self._owners.get(candidate)
            if owner_key is None:
                # A key change inside one complete snapshot (real providers, no
                # hints): the new key carries the hardware id, the old visible
                # ordinal key is no longer in the upstream set. Take over that
                # key so the UI morphs and P9 sees one device.
                migration = self._visible_migration(identity, live, visible)
                if migration is not None:
                    old_legacy, old_alias = migration
                    old = old_alias["stable"]
                    inherited = Identity(provider=identity.provider,
                                         vid=old_alias.get("vid", "0000"),
                                         pid=old_alias.get("pid", "0000"),
                                         name=old_alias.get("name", ""))
                    upgraded = identity.with_defaults(inherited)
                    upgraded_key = upgraded.hw_key()
                    if upgraded_key and upgraded_key != old:
                        return self._adopt(legacy_key, upgraded_key, upgraded, now,
                                           old, reason="same-round-key-change")
                # Backwards-compatible path for direct KeyStore users and old
                # repro scripts (no visibility given): pair by identity
                # fingerprint, unique non-live candidate only. StateStore always
                # passes visible_stables and uses the stricter path above.
                if visible is None and live_known:
                    helper = self._fingerprint_alias(identity.fingerprint(), live)
                    if helper is not None:
                        old = helper["stable"]
                        if old != candidate:
                            return self._adopt(legacy_key, candidate, identity, now,
                                               old, reason="identity-upgraded")
                self._register(legacy_key, candidate, identity, now)
                return Resolved(candidate, None, identity, True)
            owner = self._aliases.get(owner_key)
            same_device = (owner is not None and owner_key != legacy_key
                           and (owner_key not in live
                                or now - float(owner.get("last_seen", 0.0)) > STALE_AFTER))
            if same_device:
                # The same physical device, re-enumerated under a changed upstream
                # key: keep the stable key, just point the new alias at it.
                self._register(legacy_key, candidate, identity, now)
                return Resolved(candidate, None, identity, True)
            # Two live devices claim the same hardware id (protocol.md §3 does
            # not allow duplicates): fall back to a persisted ordinal.
            log.warning("device identity collision for %s; using an ordinal key", candidate)
        fingerprint = identity.fingerprint()
        reuse = self._reuse_by_fingerprint(fingerprint, live, live_known)
        if reuse is not None:
            # Fast re-enumeration (xinput slot / Steam interface, audit P1-2): the
            # old upstream key is gone from this complete snapshot, so the device
            # is no longer "live" under it. Reusing its ordinal immediately is
            # what stops u1->u2 churn; the old 30 s guard only made it worse.
            stable = reuse["stable"]
            self._register(legacy_key, stable, identity, now)
            return Resolved(stable, None, identity, True)
        stable = self._allocate_ordinal(identity)
        self._register(legacy_key, stable, identity, now)
        return Resolved(stable, None, identity, True)

    # ------------------------------------------------------------ internals
    def _touch(self, alias: Dict[str, Any], identity: Identity, now: float) -> None:
        alias["last_seen"] = now
        if identity.name:
            alias["name"] = identity.name
        if identity.vid != "0000":
            alias["vid"] = identity.vid
        if identity.pid != "0000":
            alias["pid"] = identity.pid
        self._dirty = True
        self._maybe_save(now)

    def _register(self, legacy_key: str, stable: str, identity: Identity,
                  now: float) -> None:
        entry = self._aliases.get(legacy_key)
        if entry is None:
            entry = {"first_seen": now}
            self._aliases[legacy_key] = entry
        entry.update({
            "stable": stable,
            "provider": identity.provider,
            "vid": identity.vid,
            "pid": identity.pid,
            "name": identity.name,
            "last_seen": now,
        })
        self._owners[stable] = legacy_key
        self._dirty = True
        self._maybe_save(now)

    def _alias_fingerprint(self, alias: Dict[str, Any]) -> str:
        return Identity(provider=alias.get("provider", ""),
                        vid=alias.get("vid", "0000"),
                        pid=alias.get("pid", "0000"),
                        name=alias.get("name", "")).fingerprint()

    def _visible_migration(self, identity: Identity, live: set,
                           visible: Optional[set]) -> Optional[Tuple[str, Dict[str, Any]]]:
        """The single visible key this new hardware-id key takes over.

        Safety rules (lead review, P1/P2):
        * same provider, same normalised device name;
        * compatible vid/pid (an unknown ``0000`` matches anything, because a
          logitech unit-id key does not carry the product id);
        * the old upstream key must be gone from this snapshot (not live);
        * the old stable key must still be visible (or very recently gone, or
          hidden by the user - a hidden device must migrate too);
        * exactly one candidate - two identical devices visible at the same time
          means we cannot tell which one changed, so no replaces is emitted.
        The old key may itself be a hardware key (PlayStation USB instance change,
        audit P2-5), not only an ordinal.
        """
        if visible is None:
            return None
        provider = identity.provider
        wanted = _name_key(identity.name)
        if not wanted:
            return None
        matches: List[Tuple[str, Dict[str, Any]]] = []
        for legacy, alias in self._aliases.items():
            stable = alias.get("stable", "")
            if alias.get("provider") != provider:
                continue
            if _name_key(alias.get("name", "")) != wanted:
                continue
            if stable not in visible or legacy in live:
                continue
            if not _compatible_id(identity.vid, alias.get("vid", "0000")):
                continue
            if not _compatible_id(identity.pid, alias.get("pid", "0000")):
                continue
            matches.append((legacy, alias))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log.info("ambiguous key migration for %r: %d visible candidates for %s; "
                     "no replaces (two identical devices are present)",
                     identity.name, len(matches), identity.hw_key())
        return None

    def _fingerprint_alias(self, fingerprint: str,
                           live: set) -> Optional[Dict[str, Any]]:
        """The single non-live alias with this fingerprint (any stable shape).

        Used only when no visible-key set is available (direct KeyStore API and
        the independent audit repros). Ambiguous candidates (two identical
        devices) return None so nothing is merged blindly.
        """
        matches: List[Tuple[float, str, Dict[str, Any]]] = []
        for legacy, alias in self._aliases.items():
            if self._alias_fingerprint(alias) != fingerprint:
                continue
            if legacy in live:
                continue
            matches.append((float(alias.get("last_seen", 0.0)), legacy, alias))
        if len(matches) == 1:
            return matches[0][2]
        if len(matches) > 1:
            log.info("ambiguous fingerprint migration for %s: %d non-live aliases",
                     fingerprint, len(matches))
        return None

    def _adopt(self, legacy_key: str, stable: str, identity: Identity, now: float,
               old: str, reason: str = "identity-upgraded") -> Resolved:
        """A new upstream key for the same device: register it and record replaces."""
        self._register(legacy_key, stable, identity, now)
        self._repoint_aliases(old, stable)
        self._owners.pop(old, None)
        self._history.append({
            "at": round(now, 3),
            "provider": identity.provider,
            "from": old,
            "to": stable,
            "legacy": legacy_key,
            "reason": reason,
        })
        del self._history[:-HISTORY_LIMIT]
        self._migrations += 1
        self._dirty = True
        log.info("key migration [%s] %s -> %s (%s)", identity.provider, old, stable, reason)
        self.save(force=True)
        return Resolved(stable, old, identity, True)

    def _migrate(self, legacy_key: str, alias: Dict[str, Any], old: str,
                 new: str, identity: Identity, now: float,
                 reason: str = "identity-upgraded") -> None:
        alias["stable"] = new
        alias["last_seen"] = now
        alias["vid"] = identity.vid
        alias["pid"] = identity.pid
        if identity.name:
            alias["name"] = identity.name
        self._repoint_aliases(old, new)
        self._owners.pop(old, None)
        self._owners[new] = legacy_key
        self._history.append({
            "at": round(now, 3),
            "provider": identity.provider,
            "from": old,
            "to": new,
            "legacy": legacy_key,
            "reason": reason,
        })
        del self._history[:-HISTORY_LIMIT]
        self._migrations += 1
        self._dirty = True
        log.info("key migration [%s] %s -> %s (%s)", identity.provider, old, new, reason)
        self.save(force=True)

    def _repoint_aliases(self, old: str, new: str) -> None:
        """Any other upstream key that used to map to ``old`` now maps to ``new``."""
        for alias in self._aliases.values():
            if alias.get("stable") == old:
                alias["stable"] = new

    def _reuse_by_fingerprint(self, fingerprint: str, live: set,
                              live_known: bool) -> Optional[Dict[str, Any]]:
        """The single ordinal alias this slot/interface key may reuse.

        Audit P1-2: an xinput slot or Steam interface number is not identity. When
        the only alias with this fingerprint is no longer live in the current
        complete snapshot, the device was re-enumerated under another slot, so the
        old ordinal is reused immediately (the old 30 s "stale" guard caused
        u1 -> u2 churn). Two non-live candidates mean two identical devices could
        have swapped, so we refuse to guess. Without ``live_legacy`` the caller
        gave no snapshot context, so reuse is not attempted.
        """
        if not live_known:
            return None
        matches: List[Tuple[float, str, Dict[str, Any]]] = []
        for legacy, alias in self._aliases.items():
            if not _ORDINAL_RE.search(alias.get("stable", "")):
                continue
            if self._alias_fingerprint(alias) != fingerprint:
                continue
            if legacy in live:
                continue                       # still reported under this legacy key
            matches.append((float(alias.get("last_seen", 0.0)), legacy, alias))
        if len(matches) == 1:
            return matches[0][2]
        if len(matches) > 1:
            log.info("ambiguous slot re-enumeration for %s: %d non-live aliases; "
                     "allocating a new ordinal", fingerprint, len(matches))
        return None

    def _allocate_ordinal(self, identity: Identity) -> str:
        scope = f"{identity.provider}:{identity.vid}:{identity.pid}"
        n = self._next_ordinal.get(scope, 0) + 1
        self._next_ordinal[scope] = n
        return f"{scope}:u{n}"
