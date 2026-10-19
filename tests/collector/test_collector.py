"""Collector-level regressions: wake/refresh, daemon pool, watchdog, Bluetooth.

These drive the real ``Collector`` object with a synchronous stub pool and a
simulated event-driven Bluetooth source, so they are deterministic and never
spawn PowerShell.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from collector.collector import Collector
from conftest import Harness, make_status


class DummyProvider:
    def __init__(self, name: str, devices=None):
        self.name = name
        self.devices = list(devices or [])
        self.polls = 0
        self.pending = False

    def poll(self):
        self.polls += 1
        return list(self.devices)

    def diagnostics(self):
        return [f"[{self.name}] dummy"]


class DummyProtocol:
    def stat(self, *args, **kwargs):
        return None


class StubPool:
    """Collects submitted calls instead of running them in threads."""

    def __init__(self, slots: int = 8):
        self.tasks = []
        self.free_slots = slots

    def submit(self, fn, *args):
        if self.free_slots <= 0:
            return False
        self.free_slots -= 1
        self.tasks.append((fn, args))
        return True

    def close(self, timeout: float = 0.0):
        pass

    def run_all(self):
        pending, self.tasks = self.tasks, []
        for fn, args in pending:
            fn(*args)


class StubMonitor:
    watcher_alive = False

    def __init__(self, healthy: bool = True, poll_interval: float = 60.0):
        self._healthy = healthy
        self.poll_interval = poll_interval
        self.stopped = False
        self.snapshots = 0
        self.fallback_polls = 0

    def healthy(self):
        return self._healthy

    def poll_once(self):
        self.fallback_polls += 1
        self.snapshots += 1
        return []

    def stop(self, timeout: float = 0.0):
        self.stopped = True


def _collector(tmp_path, providers=("logitech", "razer"), with_monitor=False,
               tick_period=1.0, devices=None, monitor_healthy=True):
    state_providers = tuple(providers) + (("bluetooth",) if with_monitor else ())
    h = Harness(tmp_path, providers=state_providers, ttl=60.0,
                bluetooth_event=with_monitor)
    devices = devices or {}
    pollers = [DummyProvider(name, devices.get(name)) for name in providers]
    collector = Collector(
        pollers, h.settings, DummyProtocol(), h.state, h.keys,
        bluetooth_provider=None, tick_period=tick_period, fake_world=None)
    old_pool = collector._pool
    collector._pool = StubPool()
    if with_monitor:
        collector._bt_monitor = StubMonitor(healthy=monitor_healthy)
    return h, collector, old_pool


def _close(h, collector, old_pool):
    collector._stop.set()
    collector._pool = old_pool
    old_pool.close(0.05)


def _real_pool(collector, old_pool):
    """Swap the stub pool for a real daemon pool (run_once/run_for need one)."""
    old_pool.close(0.05)
    from collector.collector import DaemonPool
    collector._pool = DaemonPool(1)
    return collector._pool


def _complete_round(collector):
    collector._scheduler_wake()
    collector._pool.run_all()


# ---------------------------------------------------------------- refresh
def test_refresh_during_round_is_not_lost_and_forces_a_second_round(tmp_path):
    h, collector, old_pool = _collector(tmp_path)
    try:
        collector._scheduler_wake()                 # round 1 starts
        assert collector._round_active
        assert len(collector._pool.tasks) == 2
        collector.wake()                            # refresh arrives mid-round
        collector._scheduler_wake()                 # must NOT consume the flag
        assert len(collector._pool.tasks) == 2      # no overlapping round
        assert collector._round_active

        collector._pool.run_all()                   # complete round 1
        assert not collector._round_active

        collector._scheduler_wake()                 # queued refresh -> forced round
        assert len(collector._pool.tasks) == 2
        # both provider polls were "fast" polls: lead expectation fast_polls == 2
        assert collector.fast_polls == 2
    finally:
        _close(h, collector, old_pool)


def test_refresh_while_round_open_polls_finished_providers_immediately(tmp_path):
    """The audit repro2 timing: a finished provider is re-polled on refresh even
    though another provider still holds the round open."""
    h, collector, old_pool = _collector(tmp_path)
    try:
        collector._scheduler_wake()
        assert len(collector._pool.tasks) == 2
        # run only the first provider (logitech), leave razer in flight
        fn, args = collector._pool.tasks.pop(0)
        fn(*args)
        assert collector.tasks["razer"].in_flight
        assert collector._round_active
        collector.wake()
        # the pool still holds the blocked razer task; logitech was re-submitted
        assert len(collector._pool.tasks) == 2
        assert collector._pool.tasks[-1][1][0].name == "logitech"
        assert collector.fast_polls == 1
    finally:
        _close(h, collector, old_pool)


def test_provider_refresh_only_polls_that_provider(tmp_path):
    h, collector, old_pool = _collector(tmp_path)
    try:
        _complete_round(collector)
        collector.fast_polls = 0

        collector.wake("logitech")
        collector._scheduler_wake()
        submitted = [args[0].name for _, args in collector._pool.tasks]
        assert submitted == ["logitech"]            # razer is not due yet
        assert collector.fast_polls == 1
    finally:
        _close(h, collector, old_pool)


# ---------------------------------------------------------------- watchdog / pool
def test_pool_capacity_defers_instead_of_piling_up(tmp_path):
    h, collector, old_pool = _collector(tmp_path)
    collector._pool.free_slots = 0
    try:
        collector._scheduler_wake()
        assert collector._pool.tasks == []
        assert not collector._round_active
        assert not any(task.in_flight for task in collector.tasks.values())
    finally:
        _close(h, collector, old_pool)


def test_stuck_provider_is_reaped_and_its_device_can_expire(tmp_path):
    devices = {
        "logitech": [make_status("logitech:c547:1", "G502 X PLUS", 68,
                                 source="logitech", kind="mouse")],
        "razer": [make_status("razer:0554:1a2b3c4d", "BlackShark V2 Pro", 55,
                              source="razer", kind="headset")],
    }
    h, collector, old_pool = _collector(tmp_path, devices=devices)
    try:
        _complete_round(collector)
        assert len(h.state.snapshot_devices()) == 2
        logitech = collector.tasks["logitech"]
        logitech.in_flight = True
        logitech.started_mono = time.monotonic() - 1000.0
        h.state.set_in_flight({"logitech"})

        collector._scheduler_wake()                 # watchdog runs first
        assert not logitech.in_flight
        assert logitech.timeouts == 1
        assert h.state._in_flight == set()          # device no longer protected
        assert collector.stat_payload()["logitech"]["timeouts"] == 1

        h.clock.advance(200.0)                      # > max(2*TTL,10)
        h.state.on_tick()
        assert "logitech:046d:c547:u1" in {m["key"] for m in h.sink.by_type("gone")}
    finally:
        _close(h, collector, old_pool)


# ---------------------------------------------------------------- bluetooth
def test_bluetooth_fallback_polls_at_the_configured_interval(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        with_monitor=True, monitor_healthy=False)
    try:
        monitor = collector._bt_monitor
        collector._bt_next_poll = 0.0
        collector._scheduler_wake()
        assert any(len(args) == 1 and args[0] is monitor
                   for _, args in collector._pool.tasks)
        collector._pool.run_all()                   # runs poll_bluetooth once

        collector._scheduler_wake()                 # not due again (60 s interval)
        assert not any(len(args) == 1 and args[0] is monitor
                       for _, args in collector._pool.tasks)
        collector._bt_next_poll = time.monotonic() - 1.0
        collector._scheduler_wake()                 # interval elapsed: one more
        collector._pool.run_all()
        assert monitor.fallback_polls == 2
    finally:
        _close(h, collector, old_pool)


def test_healthy_bluetooth_watcher_never_falls_back(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        with_monitor=True, monitor_healthy=True)
    try:
        collector._bt_next_poll = 0.0
        collector._scheduler_wake()
        assert not any(len(args) == 1 and args[0] is collector._bt_monitor
                       for _, args in collector._pool.tasks)
    finally:
        _close(h, collector, old_pool)


def test_run_once_waits_for_the_first_bluetooth_snapshot(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        with_monitor=True, tick_period=0.05)
    pool = _real_pool(collector, old_pool)
    status = make_status("bt:001122334455", "WH-1000XM5", 42,
                         source="bluetooth", kind="bluetooth")

    def deliver():
        time.sleep(0.2)
        h.state.on_bluetooth_snapshot([status])

    threading.Thread(target=deliver, daemon=True).start()
    started = time.monotonic()
    try:
        assert collector.run_once(timeout=5.0, bluetooth_timeout=2.0) is True
        elapsed = time.monotonic() - started
        assert elapsed >= 0.18                  # it really waited for the snapshot
        keys = {d["key"] for d in h.state.snapshot_devices()}
        assert "bluetooth:0000:0000:001122334455" in keys
    finally:
        pool.close(0.2)
        _close(h, collector, old_pool)


def test_run_once_bounds_the_bluetooth_wait(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        with_monitor=True, tick_period=0.05)
    pool = _real_pool(collector, old_pool)
    started = time.monotonic()
    try:
        assert collector.run_once(timeout=5.0, bluetooth_timeout=0.3) is True
        elapsed = time.monotonic() - started
        assert 0.25 <= elapsed < 1.5            # bounded, no 90 s hang
        assert h.state.bluetooth_snapshots == 0
    finally:
        pool.close(0.2)
        _close(h, collector, old_pool)


def test_run_for_stops_at_the_deadline(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        tick_period=0.05)
    pool = _real_pool(collector, old_pool)
    started = time.monotonic()
    try:
        collector.run_for(0.3, ready_timeout=5.0)
        elapsed = time.monotonic() - started
        assert 0.25 <= elapsed < 1.5
    finally:
        pool.close(0.2)
        _close(h, collector, old_pool)


# ---------------------------------------------------------------- bluetooth interval
def test_stat_exposes_the_configured_bt_watch_interval(tmp_path):
    h, collector, old_pool = _collector(tmp_path, providers=("logitech",),
                                        with_monitor=True)
    try:
        collector._bt_watch_ms = 10000
        payload = collector.stat_payload()
        assert payload["bluetooth"]["watch_ms"] == 10000
    finally:
        _close(h, collector, old_pool)


def test_collector_start_configures_the_bt_watch_interval(tmp_path, monkeypatch):
    from collector import btconfig
    import collector.collector as collector_module

    class StubMonitorThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True

        def stop(self, timeout=0.0):
            pass

    h = Harness(tmp_path, providers=(), ttl=60.0)
    settings = h.settings
    seen = {}

    def fake_configure(settings_arg):
        seen["settings"] = settings_arg
        return 10000

    monkeypatch.setattr(collector_module, "BluetoothMonitor", StubMonitorThread)
    monkeypatch.setattr(btconfig, "configure", fake_configure)
    bluetooth_provider = type("BTProvider", (), {"name": "bluetooth"})()
    collector = Collector(
        [], settings, DummyProtocol(), h.state, h.keys,
        bluetooth_provider=bluetooth_provider, fake_world=object())
    try:
        collector.start()
        assert seen["settings"] is settings
        assert collector._bt_watch_ms == 10000
    finally:
        collector._stop.set()
        collector._pool.close(0.05)


# ---------------------------------------------------------------- child processes
def test_child_registry_tracks_and_terminates_subprocess_run_children():
    from collector import procs
    registry = procs.install()
    try:
        finished = subprocess.run([sys.executable, "-c", "pass"], timeout=30,
                                  capture_output=True)
        assert finished.returncode == 0
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        assert registry.count() >= 1
        killed = procs.terminate_all(timeout=0.5)
        assert killed >= 1
        assert proc.poll() is not None
        assert registry.count() == 0
    finally:
        registry.uninstall()


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
def test_job_object_kills_children_when_the_collector_dies():
    """Hard-kill P10: no Python cleanup runs, so KILL_ON_JOB_CLOSE must reap them."""
    from collector import procs
    registry = procs.install()
    if not registry.job_active:
        registry.uninstall()
        pytest.skip("child Job Object could not be created here")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        registry.uninstall()                # closes the job handle, like process death
        proc.wait(timeout=5.0)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
