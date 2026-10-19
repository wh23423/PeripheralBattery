"""Runtime configuration of the upstream Bluetooth watcher's poll schedule.

``collector/providers/bluetooth.py`` is copied verbatim from upstream and stays
untouched. Its ``WATCH_SCRIPT`` polls the WinRT connection state every 2 s
(``Start-Sleep -Milliseconds 2000``), which costs ~0.5 % of one core in a
permanently running PowerShell process - the dominant part of the hidden-state
P2 budget. The collector patches the PowerShell *text* once, just before the
watcher starts:

* the fixed sleep is replaced by a due-time driven one (P1-11): the loop sleeps
  until the earliest of the connection-check beat (``bt_watch_ms``), the oldest
  queued battery-snapshot due time (``$queue[0]``) and the periodic full snapshot
  (``$nextFull``), with a 100 ms floor so a due event cannot busy-loop. Without
  that, a 30 s sleep collapsed the queued re-reads of a connection change
  (+0/+3/+8/+15 s) and the cold-start retries (+3/+8/+15/+30 s) into a single
  snapshot at the poll interval;
* the connection state is still only read every ``bt_watch_ms``: the queue wake
  ups for battery snapshots must not start a WinRT connection query each;
* default 30000 ms (15x fewer wake-ups than the upstream 2 s poll). Reaching the
  hidden-state P2 headroom matters more than a sub-10 s connect latency: the
  product accepts a connection-state detection delay of up to 30 s;
* ``config.json:bt_watch_ms`` overrides it, ``PB_BT_WATCH_MS`` overrides both
  (runtime knob for testing / power users), clamped to 2000..300000 ms;
* battery snapshots are unchanged in content: a full snapshot on a connection
  change (retries at +3/+8/+15 s) and once a minute. They now really fire at
  those times instead of being swallowed by the sleep;
* cold-start fix (F3): Windows' first PnP/WinRT pass after boot can miss the
  connected device, so whenever the first snapshot lists any paired Bluetooth
  root the watcher re-reads once at +3/+8/+15/+30 s. This is deliberately based
  on roots rather than "has an OK device / has battery props": a paired but
  offline device (e.g. a cached WH-1000XM4) has status OK and cached battery and
  must not suppress the retry.

``next_wake_ms`` is the Python mirror of the generated sleep computation: the
schedule can be unit tested deterministically without spawning PowerShell.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional, Sequence

log = logging.getLogger("halo_battery")

DEFAULT_BT_WATCH_MS = 30000
MIN_BT_WATCH_MS = 2000
MAX_BT_WATCH_MS = 300000

MIN_SLEEP_MS = 100            # floor of the dynamic sleep (no busy loop)
FULL_SNAPSHOT_S = 60          # periodic full-snapshot cadence in the script
CONNECT_RETRY_S = (0, 3, 8, 15)   # snapshot queue after a connection change
COLD_RETRY_S = (3, 8, 15, 30)     # first-snapshot (cold start) retry queue

_SLEEP_TOKEN = "Start-Sleep -Milliseconds 2000"
_SLEEP_RE = re.compile(r"Start-Sleep -Milliseconds \d+")

# The upstream loop head that the P1-11 patch replaces, kept verbatim so an
# upstream text change is detected (and logged) instead of silently producing a
# script that never wakes up for the queued retries.
_UPSTREAM_LOOP_HEAD = """  Start-Sleep -Milliseconds 2000
  $now = Get-Date
  if ($roots.Count -gt 0 -and -not $script:winrtErr) {
    $key = Get-ConnKey (Get-BtConn $roots)
    if ($key -ne $prev) {
      $prev = $key; $queue.Clear()
      foreach ($s in 0, 3, 8, 15) { $queue.Add($now.AddSeconds($s)) }
    }
  }"""

# Replacement for _UPSTREAM_LOOP_HEAD. ``%MS%`` is bt_watch_ms and ``%FLOOR%``
# is MIN_SLEEP_MS; the generated PowerShell is intentionally the same logic as
# next_wake_ms(): wake at the earliest due time, only query the connection state
# on its own beat, and let the (unchanged) due/60 s snapshot block below run.
_SCHEDULE_BLOCK = """  # --- collector patch: sleep only until the next due event (P1-11) ----------
  # A fixed Start-Sleep collapses the queued +0/+3/+8/+15 s (connection change)
  # and +3/+8/+15/+30 s (cold start) snapshot re-reads into one wake-up. Instead:
  # sleep until the earliest of the connection-check beat ($nextConnCheck), the
  # oldest queued snapshot due time ($queue[0]) and the periodic full snapshot
  # ($nextFull), never less than %FLOOR% ms. The WinRT connection state is only
  # queried on its own beat, not on every queue wake-up.
  if ($null -eq $nextConnCheck) { $nextConnCheck = (Get-Date).AddMilliseconds(%MS%) }
  $wake = $nextConnCheck
  if ($queue.Count -gt 0 -and $queue[0] -lt $wake) { $wake = $queue[0] }
  if ($nextFull -lt $wake) { $wake = $nextFull }
  $sleepMs = [int](($wake - (Get-Date)).TotalMilliseconds)
  if ($sleepMs -lt %FLOOR%) { $sleepMs = %FLOOR% }
  Start-Sleep -Milliseconds $sleepMs
  $now = Get-Date
  if ($now -ge $nextConnCheck) {
    $nextConnCheck = $now.AddMilliseconds(%MS%)
    if ($roots.Count -gt 0 -and -not $script:winrtErr) {
      $key = Get-ConnKey (Get-BtConn $roots)
      if ($key -ne $prev) {
        $prev = $key; $queue.Clear()
        foreach ($s in 0, 3, 8, 15) { $queue.Add($now.AddSeconds($s)) }
      }
    }
  }"""

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


def next_wake_ms(now_ms: float, next_conn_check_ms: float,
                 queue: Sequence[float], next_full_ms: float,
                 floor_ms: int = MIN_SLEEP_MS) -> int:
    """Python mirror of the generated loop's dynamic sleep (P1-11).

    The PowerShell block in ``_SCHEDULE_BLOCK`` computes exactly this: how many
    milliseconds to sleep so that the loop wakes at the earliest of the
    connection-check beat, the oldest queued snapshot due time and the periodic
    full snapshot, but never for less than ``floor_ms``. Keeping the model here
    makes the schedule testable without spawning PowerShell.
    """
    due = [float(next_conn_check_ms), float(next_full_ms)]
    if queue:
        due.append(float(queue[0]))
    return max(int(floor_ms), int(min(due) - float(now_ms)))


def build_watch_script(original: str, interval_ms: int) -> str:
    """Return ``original`` with the fixed sleep replaced by the due schedule.

    Pure text transform: tests can assert/execute the result without touching
    ``providers.bluetooth``; ``patch_watch_script`` assigns it to the module
    attribute (the copied source file is never modified).
    """
    interval_ms = int(interval_ms)
    patched = original
    block = (_SCHEDULE_BLOCK.replace("%MS%", str(interval_ms))
             .replace("%FLOOR%", str(MIN_SLEEP_MS)))
    if _UPSTREAM_LOOP_HEAD in patched:
        patched = patched.replace(_UPSTREAM_LOOP_HEAD, block, 1)
    else:
        # Upstream changed its loop text: fall back to the old fixed-interval
        # behaviour (fewer wake-ups, but the queued retries would collapse) and
        # make the degradation visible in the log.
        token = f"Start-Sleep -Milliseconds {interval_ms}"
        fallback = _SLEEP_RE.sub(token, patched, count=1)
        if fallback == patched:
            log.warning("Bluetooth WATCH_SCRIPT has no Start-Sleep to patch; "
                        "keeping upstream interval")
        else:
            log.warning("Bluetooth WATCH_SCRIPT loop changed; keeping a fixed "
                        "%d ms sleep instead of the due schedule", interval_ms)
        patched = fallback
    if _RETRY_ANCHOR in patched and _RETRY_BLOCK not in patched:
        patched = patched.replace(_RETRY_ANCHOR,
                                  _RETRY_ANCHOR + "\n" + _RETRY_BLOCK, 1)
    elif _RETRY_ANCHOR not in patched:
        log.warning("Bluetooth WATCH_SCRIPT anchor for the first-snapshot retry "
                    "was not found; upstream probably changed")
    return patched


def patch_watch_script(interval_ms: int) -> str:
    """Patch the in-memory ``providers.bluetooth.WATCH_SCRIPT`` text.

    Returns the patched script. Only ``providers.bluetooth.WATCH_SCRIPT`` (a
    module attribute) is changed; the copied source file is never modified.
    """
    global _original_script, _applied_ms
    from .providers import bluetooth

    if _original_script is None:
        _original_script = bluetooth.WATCH_SCRIPT
    patched = build_watch_script(_original_script, int(interval_ms))
    bluetooth.WATCH_SCRIPT = patched
    _applied_ms = int(interval_ms)
    return patched


def configure(settings=None) -> int:
    """Resolve the interval, patch the watcher script and log the result."""
    ms = resolve_bt_watch_ms(settings)
    patch_watch_script(ms)
    log.info("bluetooth watcher: connection check every %d ms "
             "(bt_watch_ms / PB_BT_WATCH_MS, floor %d ms, upstream was %s ms); "
             "snapshots wake at the earliest due time (connection change "
             "+0/+3/+8/+15 s, cold-start retry +3/+8/+15/+30 s, periodic full "
             "every %d s)",
             ms, MIN_BT_WATCH_MS,
             _SLEEP_TOKEN.rsplit(" ", 1)[-1], FULL_SNAPSHOT_S)
    return ms


def applied_ms() -> Optional[int]:
    return _applied_ms


def original_script() -> Optional[str]:
    return _original_script
