"""Runtime configuration of the upstream Bluetooth watcher's poll interval.

``collector/providers/bluetooth.py`` is copied verbatim from upstream and stays
untouched. Its ``WATCH_SCRIPT`` polls the WinRT connection state every 2 s
(``Start-Sleep -Milliseconds 2000``), which costs ~0.5 % of one core in a
permanently running PowerShell process - the dominant part of the hidden-state
P2 budget. The collector patches the PowerShell *text* once, just before the
watcher starts:

* default 30000 ms (15x fewer wake-ups than the upstream 2 s poll). Reaching the
  hidden-state P2 headroom matters more than a sub-10 s connect latency: the
  product accepts a connection-state detection delay of up to 30 s;
* ``config.json:bt_watch_ms`` overrides it, ``PB_BT_WATCH_MS`` overrides both
  (runtime knob for testing / power users), clamped to 2000..300000 ms;
* battery snapshots are unchanged: the script still takes a full snapshot on a
  connection change (+3/+8/+15 s retries) and once a minute. The only product
  trade-off is that a connect/disconnect is noticed up to ``bt_watch_ms`` later
  (30 s by default);
* cold-start fix (F3): Windows' first PnP/WinRT pass after boot can miss the
  connected device, so whenever the first snapshot lists any paired Bluetooth
  root the watcher re-reads once at +3/+8/+15/+30 s. This is deliberately based
  on roots rather than "has an OK device / has battery props": a paired but
  offline device (e.g. a cached WH-1000XM4) has status OK and cached battery and
  must not suppress the retry.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger("halo_battery")

DEFAULT_BT_WATCH_MS = 30000
MIN_BT_WATCH_MS = 2000
MAX_BT_WATCH_MS = 300000

_SLEEP_TOKEN = "Start-Sleep -Milliseconds 2000"
_SLEEP_RE = re.compile(r"Start-Sleep -Milliseconds \d+")

# F3 (revised): the very first Get-BtSnapshot after a cold start occasionally
# only returns a paired-but-offline device (with status OK and a *cached* battery
# level) while the connected device Windows can actually read is missing; the
# next full snapshot would be a minute away. Gating on "has OK device / has
# battery props" therefore classified that snapshot as healthy and lost the fix.
# New rule: if the first snapshot lists any Bluetooth roots at all, queue one
# one-shot battery re-read at +3/+8/+15/+30 s through the script's existing
# $queue/$due mechanism. Only the FIRST snapshot does this (the queue is then
# consumed); connection changes keep their own +3/+8/+15 schedule.
_RETRY_ANCHOR = ("$roots = $snap.roots; $prev = Get-ConnKey $snap.aep; "
                 "$nextFull = (Get-Date).AddSeconds(60)")
_RETRY_BLOCK = """# --- collector patch: first cold-start snapshot may miss devices -------------
# Any paired Bluetooth root => retry once at +3/+8/+15/+30 s. A paired-but-offline
# device must not count as "healthy": Windows can report it with status OK and a
# cached battery while the connected device is missing from the first snapshot.
$now = Get-Date
if ($snap.roots.Count -gt 0) {
  foreach ($s in 3, 8, 15, 30) { $queue.Add($now.AddSeconds($s)) }
}"""

_original_script: Optional[str] = None
_applied_ms: Optional[int] = None


def resolve_bt_watch_ms(settings=None) -> int:
    """Effective interval: settings value, then PB_BT_WATCH_MS, clamped."""
    value = DEFAULT_BT_WATCH_MS
    if settings is not None:
        try:
            value = settings.get("bt_watch_ms", DEFAULT_BT_WATCH_MS)
        except Exception:
            value = DEFAULT_BT_WATCH_MS
    env_value = os.environ.get("PB_BT_WATCH_MS")
    if env_value not in (None, ""):
        value = env_value
    try:
        ms = int(value)
    except (TypeError, ValueError):
        log.warning("invalid bt_watch_ms %r; using %d", value, DEFAULT_BT_WATCH_MS)
        ms = DEFAULT_BT_WATCH_MS
    if ms < MIN_BT_WATCH_MS:
        log.warning("bt_watch_ms %d is below the %d ms floor", ms, MIN_BT_WATCH_MS)
        ms = MIN_BT_WATCH_MS
    elif ms > MAX_BT_WATCH_MS:
        log.warning("bt_watch_ms %d is above the %d ms ceiling", ms, MAX_BT_WATCH_MS)
        ms = MAX_BT_WATCH_MS
    return ms


def patch_watch_script(interval_ms: int) -> str:
    """Replace the 2 s connection poll in the module's WATCH_SCRIPT text.

    Returns the patched script. Only ``providers.bluetooth.WATCH_SCRIPT`` (a
    module attribute) is changed; the copied source file is never modified.
    """
    global _original_script, _applied_ms
    from .providers import bluetooth

    if _original_script is None:
        _original_script = bluetooth.WATCH_SCRIPT
    token = f"Start-Sleep -Milliseconds {int(interval_ms)}"
    if _SLEEP_TOKEN in _original_script:
        patched = _original_script.replace(_SLEEP_TOKEN, token)
    else:                                   # upstream changed its text: be robust
        patched = _SLEEP_RE.sub(token, _original_script, count=1)
        if patched == _original_script:
            log.warning("Bluetooth WATCH_SCRIPT has no Start-Sleep to patch; "
                        "keeping upstream interval")
    if _RETRY_ANCHOR in patched and _RETRY_BLOCK not in patched:
        patched = patched.replace(_RETRY_ANCHOR,
                                  _RETRY_ANCHOR + "\n" + _RETRY_BLOCK, 1)
    elif _RETRY_ANCHOR not in patched:
        log.warning("Bluetooth WATCH_SCRIPT anchor for the first-snapshot retry "
                    "was not found; upstream probably changed")
    bluetooth.WATCH_SCRIPT = patched
    _applied_ms = int(interval_ms)
    return patched


def configure(settings=None) -> int:
    """Resolve the interval, patch the watcher script and log the result."""
    ms = resolve_bt_watch_ms(settings)
    patch_watch_script(ms)
    log.info("bluetooth watcher connection poll interval: %d ms "
             "(bt_watch_ms / PB_BT_WATCH_MS, floor %d ms, upstream was %s ms); "
             "first-snapshot retry at +3/+8/+15/+30 s when the cold-start "
             "snapshot is missing devices",
             ms, MIN_BT_WATCH_MS,
             _SLEEP_TOKEN.rsplit(" ", 1)[-1])
    return ms


def applied_ms() -> Optional[int]:
    return _applied_ms


def original_script() -> Optional[str]:
    return _original_script
