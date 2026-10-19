"""The runtime patch of the upstream Bluetooth watcher's poll schedule.

The copied ``providers/bluetooth.py`` must stay byte-identical; only the
module-level ``WATCH_SCRIPT`` attribute is patched before the watcher starts.

P1-11: a fixed sleep collapsed the queued +0/+3/+8/+15 s re-reads after a
connection change and the +3/+8/+15/+30 s cold-start retries into a single
snapshot at the poll interval. The patched loop sleeps until the earliest due
time; the Python model (``btconfig.next_wake_ms``) is tested here, and a
PowerShell simulation of the *generated* script verifies the real timestamps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from collector import btconfig
from collector.providers import bluetooth as bt
from collector.settings import Settings


def _settings(tmp_path):
    settings = Settings(str(tmp_path / "data"))
    settings.ensure_dirs()
    return settings


def _clear_env(monkeypatch):
    monkeypatch.delenv("PB_BT_WATCH_MS", raising=False)


def _configured_script(tmp_path, monkeypatch, env_ms=None):
    _clear_env(monkeypatch)
    if env_ms is not None:
        monkeypatch.setenv("PB_BT_WATCH_MS", str(env_ms))
    btconfig.configure(_settings(tmp_path))
    return bt.WATCH_SCRIPT


def test_default_is_30000_and_script_is_patched(tmp_path, monkeypatch):
    script = _configured_script(tmp_path, monkeypatch)
    assert btconfig.applied_ms() == 30000
    # no fixed sleep left: the loop computes its sleep from the next due time
    assert "Start-Sleep -Milliseconds $sleepMs" in script
    assert "Start-Sleep -Milliseconds 2000" not in script
    assert "Start-Sleep -Milliseconds 30000" not in script
    assert "$nextConnCheck = (Get-Date).AddMilliseconds(30000)" in script
    # connection detection and the battery snapshot schedule are untouched
    for fragment in ("Get-ConnKey (Get-BtConn $roots)",
                     "foreach ($s in 0, 3, 8, 15)",
                     "AddSeconds(60)",
                     "Get-BtSnapshot",
                     "GetProcessById($parent)"):
        assert fragment in script, fragment
    # the upstream source file itself was not modified and still has the anchor
    source = Path(bt.__file__).read_text(encoding="utf-8")
    assert "Start-Sleep -Milliseconds 2000" in source
    assert btconfig._UPSTREAM_LOOP_HEAD in source


def test_first_snapshot_retry_queue_is_installed(tmp_path, monkeypatch):
    """F3: a cold-start snapshot retries at +3/+8/+15/+30 s when BT roots exist."""
    script = _configured_script(tmp_path, monkeypatch)
    assert "foreach ($s in 3, 8, 15, 30)" in script
    assert "if ($snap.roots.Count -gt 0)" in script
    # the broken gate must be gone: a paired-but-offline device (status OK with a
    # cached battery) may not suppress the retry
    assert "$_.status" not in btconfig._RETRY_BLOCK
    assert "hasBattery" not in btconfig._RETRY_BLOCK
    assert "hasOnline" not in btconfig._RETRY_BLOCK
    # only the first snapshot does this; it sits after the initial Emit
    assert script.count("foreach ($s in 3, 8, 15, 30)") == 1
    assert script.index("Emit $snap") < script.index("foreach ($s in 3, 8, 15, 30)")
    # the connection-change schedule is still the original one and comes later
    # (inside the watcher loop, after the first-snapshot retry block)
    assert "foreach ($s in 0, 3, 8, 15)" in script
    assert script.index("foreach ($s in 0, 3, 8, 15)") > \
        script.index("foreach ($s in 3, 8, 15, 30)")
    # markers/property names used by the retry exist in the upstream snapshot
    assert "roots" in script and "devs" in script and "props" in script
    # the original upstream text stays free of the retry block
    assert "foreach ($s in 3, 8, 15, 30)" not in btconfig.original_script()


# ---------------------------------------------------------------- P1-11 schedule
def test_sleep_wakes_at_the_earliest_due_time_not_a_fixed_interval(tmp_path,
                                                                   monkeypatch):
    script = _configured_script(tmp_path, monkeypatch)
    assert "$wake = $nextConnCheck" in script
    assert "if ($queue.Count -gt 0 -and $queue[0] -lt $wake) { $wake = $queue[0] }" \
        in script
    assert "if ($nextFull -lt $wake) { $wake = $nextFull }" in script
    assert "$sleepMs = [int](($wake - (Get-Date)).TotalMilliseconds)" in script
    assert "if ($sleepMs -lt 100) { $sleepMs = 100 }" in script
    assert "Start-Sleep -Milliseconds $sleepMs" in script
    # the WinRT connection query only runs on its own beat, not on queue wakes
    assert "if ($now -ge $nextConnCheck)" in script
    assert script.index("if ($now -ge $nextConnCheck)") < \
        script.index("Get-ConnKey (Get-BtConn $roots)")
    assert script.count("Get-ConnKey (Get-BtConn $roots)") == 1
    # the connection beat is re-armed after every check, and it is bt_watch_ms
    assert script.count("$nextConnCheck = $now.AddMilliseconds(30000)") == 1


def test_watcher_command_uses_the_patched_schedule(tmp_path, monkeypatch):
    """The real BluetoothWatcher must hand the patched text to PowerShell."""
    _configured_script(tmp_path, monkeypatch)
    from collector.providers.bluetooth import BluetoothProvider, BluetoothWatcher

    watcher = BluetoothWatcher(BluetoothProvider(), lambda devices: None)
    command = watcher.command()
    assert command[0] == "powershell"
    script = command[-1]
    assert "%PPID%" not in script
    assert f"$parent = {os.getpid()}" in script
    assert "Start-Sleep -Milliseconds $sleepMs" in script
    assert "$nextConnCheck = (Get-Date).AddMilliseconds(30000)" in script
    assert "Start-Sleep -Milliseconds 30000" not in script


def test_next_wake_ms_model_matches_the_generated_formula():
    assert btconfig.MIN_SLEEP_MS == 100
    # the earliest of connection beat / queue due / full snapshot wins
    assert btconfig.next_wake_ms(0, 30000, [3000], 60000) == 3000
    assert btconfig.next_wake_ms(0, 2000, [], 60000) == 2000
    assert btconfig.next_wake_ms(0, 30000, [], 60000) == 30000
    assert btconfig.next_wake_ms(2500, 30000, [3000], 60000) == 500
    # a due/overdue event never busy-loops: floor 100 ms
    assert btconfig.next_wake_ms(5000, 4000, [1000], 60000) == btconfig.MIN_SLEEP_MS
    assert btconfig.next_wake_ms(0, -5000, [], 60000) == btconfig.MIN_SLEEP_MS


def _simulate(now, next_conn_check, queue, next_full, until, watch_ms,
              conn_log, snap_log):
    """Drive the loop exactly like the generated PowerShell does.

    The script emits its initial snapshot before the loop, so the model starts
    at the first sleep. Events at the upper bound are not processed.
    """
    while now < until:
        delay = btconfig.next_wake_ms(now, next_conn_check, queue, next_full)
        now += delay
        if now > until:
            break
        if now >= next_conn_check:
            conn_log.append(now)
            next_conn_check = now + watch_ms
        due = False
        while queue and queue[0] <= now:
            queue.pop(0)
            due = True
        if due or now >= next_full:
            snap_log.append(now)
            next_full = now + btconfig.FULL_SNAPSHOT_S * 1000
    return now, next_conn_check, queue, next_full


def test_model_cold_start_retries_wake_at_3_8_15_30():
    """F3: the first snapshot queues +3/+8/+15/+30; all four must really fire."""
    queue = [s * 1000 for s in btconfig.COLD_RETRY_S]
    conn_log, snap_log = [], []
    _simulate(0, btconfig.DEFAULT_BT_WATCH_MS, queue, 60000, 200000,
              btconfig.DEFAULT_BT_WATCH_MS, conn_log, snap_log)
    assert snap_log == [3000, 8000, 15000, 30000, 90000, 150000]
    # the retry wakes never triggered a WinRT connection query of their own
    assert conn_log == [30000, 60000, 90000, 120000, 150000, 180000]


def test_model_connection_change_takes_snapshots_at_0_3_8_15():
    """P1-11 acceptance: after a connection change, +0/+3/+8/+15 s snapshots."""
    change = 30000
    queue = [change + s * 1000 for s in btconfig.CONNECT_RETRY_S]
    conn_log, snap_log = [], []
    # the connection check itself already ran at ``change`` and re-armed its beat
    _simulate(change, change + btconfig.DEFAULT_BT_WATCH_MS, queue,
              change + 60000, change + 15000,
              btconfig.DEFAULT_BT_WATCH_MS, conn_log, snap_log)
    # the +0 entry is popped one 100 ms floor later in this model; the real loop
    # pops it in the same iteration (asserted exactly by the PowerShell test)
    assert [t - change for t in snap_log] == [100, 3000, 8000, 15000]
    assert conn_log == []          # queue wake-ups never query the connection


def test_model_connection_check_stays_on_its_own_beat():
    """Queue wakes must not add WinRT connection queries: only every bt_watch_ms."""
    queue = [3000, 8000, 15000, 30000]
    conn_log, snap_log = [], []
    _simulate(0, 2000, queue, 60000, 19000, 2000, conn_log, snap_log)
    assert conn_log == [2000, 4000, 6000, 8000, 10000, 12000, 14000, 16000, 18000]
    assert snap_log == [3000, 8000, 15000]


def test_unknown_upstream_text_falls_back_to_a_fixed_sleep(caplog):
    original = "while ($true) { Start-Sleep -Milliseconds 2000 }"
    patched = btconfig.build_watch_script(original, 5000)
    assert "Start-Sleep -Milliseconds 5000" in patched
    assert "Start-Sleep -Milliseconds 2000" not in patched
    assert any("loop changed" in r.getMessage() for r in caplog.records)


def _upstream_watch_script() -> str:
    """The unpatched upstream text (btconfig caches it on the first configure)."""
    if btconfig.original_script() is None:
        btconfig.patch_watch_script(btconfig.DEFAULT_BT_WATCH_MS)
    return btconfig.original_script()


def test_build_watch_script_is_pure_and_keeps_the_upstream_text():
    original = _upstream_watch_script()
    first = btconfig.build_watch_script(original, 30000)
    second = btconfig.build_watch_script(original, 30000)
    assert first == second
    assert "Start-Sleep -Milliseconds 2000" in original     # input not mutated
    assert "Start-Sleep -Milliseconds $sleepMs" not in original


# ------------------------------------------------- powershell simulation (win32)
_SIM_STUBS = r"""
# --- fake clock / WinRT for the scheduler simulation --------------------------
$script:virtualNow = [datetime]'2024-01-01T00:00:00'
$script:start = $script:virtualNow
$script:sleeps = New-Object 'System.Collections.Generic.List[double]'
$script:snaps = New-Object 'System.Collections.Generic.List[double]'
$script:conn = New-Object 'System.Collections.Generic.List[double]'
$script:changeAt = %CHANGE_AT%
function Get-Date { return $script:virtualNow }
function Start-Sleep {
  param([int]$Milliseconds)
  $script:sleeps.Add($Milliseconds)
  $script:virtualNow = $script:virtualNow.AddMilliseconds($Milliseconds)
  if (($script:virtualNow - $script:start).TotalSeconds -ge %UNTIL%) {
    $out = [pscustomobject]@{
      sleeps = $script:sleeps
      snaps = $script:snaps
      conn = $script:conn
    }
    [Console]::Out.WriteLine('RESULT ' + (ConvertTo-Json -Compress -InputObject $out))
    exit 0
  }
}
function Get-BtConn {
  param($roots)
  $elapsed = ($script:virtualNow - $script:start).TotalSeconds
  $script:conn.Add($elapsed)
  $connected = $elapsed -ge $script:changeAt
  return @([pscustomobject]@{ addr = 'AA:BB:CC:DD:EE:FF'; conn = [string]$connected })
}
function Get-BtSnapshot {
  $elapsed = ($script:virtualNow - $script:start).TotalSeconds
  $script:snaps.Add($elapsed)
  $connected = $elapsed -ge $script:changeAt
  return [pscustomobject]@{
    roots = @([pscustomobject]@{ id = 'BTHENUM\DEV_AABBCCDDEEFF\0' })
    aep = @([pscustomobject]@{ addr = 'AA:BB:CC:DD:EE:FF'; conn = $connected })
    devs = @()
    props = @()
  }
}
"""


def _run_watch_simulation(interval_ms, change_at, until, tmp_path):
    """Execute the generated WATCH_SCRIPT with stubbed clock/WinRT.

    Returns the virtual timestamps (seconds since start) of every
    Get-BtSnapshot and Get-BtConn call, so the real generated PowerShell
    schedule is asserted - not just the Python model.
    """
    script = btconfig.build_watch_script(_upstream_watch_script(), interval_ms)
    stub = (_SIM_STUBS.replace("%CHANGE_AT%", repr(float(change_at)))
                      .replace("%UNTIL%", repr(float(until))))
    body = script.replace("$parent = %PPID%", stub + "\n$parent = $PID", 1)
    path = Path(tmp_path) / f"watch-sim-{interval_ms}-{change_at}.ps1"
    path.write_text(body, encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", str(path)],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-3000:]
    lines = [line for line in result.stdout.splitlines()
             if line.startswith("RESULT ")]
    assert lines, result.stdout[-2000:]
    data = json.loads(lines[-1][len("RESULT "):])
    return ([round(float(t), 1) for t in data["snaps"]],
            [round(float(t), 1) for t in data["conn"]])


@pytest.mark.skipif(sys.platform != "win32", reason="runs the generated PowerShell")
def test_generated_powershell_really_fires_every_retry(tmp_path):
    # (a) cold start: +3/+8/+15/+30 s retries, no connection change
    snaps, conn = _run_watch_simulation(30000, 10 ** 9, 200, tmp_path)
    assert snaps == [0, 3, 8, 15, 30, 90, 150]
    assert conn == [30, 60, 90, 120, 150, 180]

    # (b) connection change detected on the first beat: +0/+3/+8/+15 s snapshots
    snaps, conn = _run_watch_simulation(30000, 30, 200, tmp_path)
    assert snaps == [0, 3, 8, 15, 30, 33, 38, 45, 105, 165]
    assert conn == [30, 60, 90, 120, 150, 180]


@pytest.mark.skipif(sys.platform != "win32", reason="runs the generated PowerShell")
def test_generated_powershell_checks_the_connection_only_on_its_beat(tmp_path):
    """PB_BT_WATCH_MS=2000: queue wakes at 3/8/15 s must not add WinRT queries."""
    snaps, conn = _run_watch_simulation(2000, 10 ** 9, 20, tmp_path)
    assert snaps == [0, 3, 8, 15]
    assert conn == [2, 4, 6, 8, 10, 12, 14, 16, 18]


@pytest.mark.skipif(sys.platform != "win32", reason="runs the generated PowerShell")
def test_retry_gate_fires_with_only_offline_paired_devices():
    """Gate regression: status OK + cached battery + roots -> retry at 3/8/15/30.

    This is the exact lead-reported failure: WH-1000XM4 alone (paired, offline,
    status OK, cached level) used to look healthy while FlyDigi was missing.
    """
    offline_only = (
        "$queue = New-Object 'System.Collections.Generic.List[datetime]'\n"
        "$now = Get-Date\n"
        "$snap = [pscustomobject]@{ "
        "devs = @([pscustomobject]@{ status = 'OK' }); "
        "props = @([pscustomobject]@{ Data = 90 }); "
        "roots = @([pscustomobject]@{}) }\n"
        + btconfig._RETRY_BLOCK + "\n"
        "(@($queue | ForEach-Object { [int]($_ - $now).TotalSeconds }) -join ',')\n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", offline_only],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3,8,15,30"

    # no paired BT roots at all -> no extra polling
    no_roots = offline_only.replace("roots = @([pscustomobject]@{})", "roots = @()")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", no_roots],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_env_overrides_settings_and_is_clamped(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.set("bt_watch_ms", 12000)
    monkeypatch.setenv("PB_BT_WATCH_MS", "6000")
    assert btconfig.resolve_bt_watch_ms(settings) == 6000
    assert btconfig.configure(settings) == 6000
    assert "$nextConnCheck = (Get-Date).AddMilliseconds(6000)" in bt.WATCH_SCRIPT

    monkeypatch.setenv("PB_BT_WATCH_MS", "500")
    assert btconfig.configure(settings) == btconfig.MIN_BT_WATCH_MS == 2000
    monkeypatch.setenv("PB_BT_WATCH_MS", "999999")
    assert btconfig.configure(settings) == btconfig.MAX_BT_WATCH_MS

    monkeypatch.delenv("PB_BT_WATCH_MS")
    assert btconfig.configure(settings) == 12000     # back to the setting


def test_reconfigure_always_starts_from_the_original_script(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _clear_env(monkeypatch)
    btconfig.configure(settings)
    monkeypatch.setenv("PB_BT_WATCH_MS", "4000")
    btconfig.configure(settings)
    assert "$nextConnCheck = (Get-Date).AddMilliseconds(4000)" in bt.WATCH_SCRIPT
    assert "AddMilliseconds(30000)" not in bt.WATCH_SCRIPT
    assert btconfig.applied_ms() == 4000
    assert "Start-Sleep -Milliseconds 2000" in btconfig.original_script()


def test_invalid_value_falls_back_to_default(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setenv("PB_BT_WATCH_MS", "not-a-number")
    assert btconfig.resolve_bt_watch_ms(settings) == btconfig.DEFAULT_BT_WATCH_MS
