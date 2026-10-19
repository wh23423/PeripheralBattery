"""The runtime patch of the upstream Bluetooth watcher interval (P2).

The copied ``providers/bluetooth.py`` must stay byte-identical; only the
module-level ``WATCH_SCRIPT`` attribute is patched before the watcher starts.
"""
from __future__ import annotations

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


def test_default_is_30000_and_script_is_patched(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    settings = _settings(tmp_path)
    assert settings.get("bt_watch_ms") == 30000
    assert btconfig.configure(settings) == 30000
    assert "Start-Sleep -Milliseconds 30000" in bt.WATCH_SCRIPT
    assert "Start-Sleep -Milliseconds 2000" not in bt.WATCH_SCRIPT
    # connection detection and the battery snapshot schedule are untouched
    for fragment in ("Get-ConnKey (Get-BtConn $roots)",
                     "foreach ($s in 0, 3, 8, 15)",
                     "AddSeconds(60)",
                     "Get-BtSnapshot",
                     "GetProcessById($parent)"):
        assert fragment in bt.WATCH_SCRIPT, fragment
    # the upstream source file itself was not modified
    source = Path(bt.__file__).read_text(encoding="utf-8")
    assert "Start-Sleep -Milliseconds 2000" in source


def test_first_snapshot_retry_queue_is_installed(tmp_path, monkeypatch):
    """F3: a cold-start snapshot retries at +3/+8/+15/+30 s when BT roots exist."""
    _clear_env(monkeypatch)
    btconfig.configure(_settings(tmp_path))
    script = bt.WATCH_SCRIPT
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
    assert "Start-Sleep -Milliseconds 6000" in bt.WATCH_SCRIPT

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
    assert "Start-Sleep -Milliseconds 4000" in bt.WATCH_SCRIPT
    assert "Start-Sleep -Milliseconds 30000" not in bt.WATCH_SCRIPT
    assert btconfig.applied_ms() == 4000
    assert "Start-Sleep -Milliseconds 2000" in btconfig.original_script()


def test_invalid_value_falls_back_to_default(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setenv("PB_BT_WATCH_MS", "not-a-number")
    assert btconfig.resolve_bt_watch_ms(settings) == btconfig.DEFAULT_BT_WATCH_MS
