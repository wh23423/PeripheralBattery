"""Collector: provider thread pool, per-category TTL scheduling, wake, commands.

One scheduler thread wakes at a fixed rate (default 1 Hz - P8 "<= 1 wake-up per
second"), submits due providers to a small thread pool, and feeds every result
into the complete-snapshot StateStore. Providers keep their own TTL cadence
(BUILD_PLAN §3.3); "gone" is never decided here, only by StateStore's global
tick from ``last_seen``.

The Bluetooth provider is special: on Windows it runs as one long-lived
PowerShell watcher (event driven, BUILD_PLAN §3.4) and its snapshots go into the
Store ungated; if the watcher cannot start, a one-shot poll every 60 s is the
fallback.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .providers.base import DeviceStatus
from .state import StateStore

log = logging.getLogger("halo_battery")


class DaemonPool:
    """Small fixed-size worker pool whose threads are daemons.

    A provider can block for seconds (Steam listens up to 5 s, xinput's WGI
    PowerShell up to 20 s). A ThreadPoolExecutor's threads are non-daemon, so a
    blocked poll kept the whole process alive for the full block after shutdown
    (measured 6.1 s). These workers are abandoned instead: ``close()`` asks them
    to stop, waits briefly, and the interpreter can exit while a poll is still
    inside C code - the provider result is simply not needed any more (P10).
    """

    def __init__(self, workers: int = 4):
        self.workers = max(1, int(workers))
        self._queue: "queue.Queue[Optional[Any]]" = queue.Queue()
        self._closed = False
        self._threads: List[threading.Thread] = []
        self._busy_lock = threading.Lock()
        self._busy = 0
        for index in range(self.workers):
            thread = threading.Thread(target=self._worker, daemon=True,
                                      name=f"collector-poll-{index}")
            thread.start()
            self._threads.append(thread)

    @property
    def free_slots(self) -> int:
        with self._busy_lock:
            return max(0, self.workers - self._busy)

    def submit(self, fn: Callable[..., Any], *args: Any) -> bool:
        """Queue one poll; False when every worker is busy or the pool is closed."""
        if self._closed:
            return False
        with self._busy_lock:
            if self._busy >= self.workers:
                return False
            self._busy += 1
        self._queue.put((fn, args))
        return True

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:                   # a poll must never kill the pool
                log.exception("provider poll worker")
            finally:
                with self._busy_lock:
                    self._busy = max(0, self._busy - 1)

    def close(self, timeout: float = 0.3) -> None:
        if self._closed:
            return
        self._closed = True
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout)


class ProviderTask:
    """Scheduling bookkeeping for one provider."""

    def __init__(self, provider: Any, interval: float, clock: Callable[[], float]):
        self.provider = provider
        self.name = getattr(provider, "name", str(provider))
        self.interval = max(1.0, float(interval))
        self.clock = clock
        self.next_due = clock()
        self.in_flight = False
        self.started_mono = 0.0
        # A blocked provider is reaped much later than its normal worst case
        # (steam 20 s, WGI 20 s, an unresponsive HID read: unbounded). The watchdog
        # only needs to be longer than a legitimate poll.
        self.timeout = max(60.0, self.interval * 3.0)
        self.polls = 0
        self.errors = 0
        self.timeouts = 0
        self.total_ms = 0.0
        self.last_ms = 0.0
        self.last_ok = 0.0

    def stats(self) -> Dict[str, Any]:
        avg = (self.total_ms / self.polls) if self.polls else 0.0
        return {"polls": self.polls, "avg_ms": round(avg), "last_ms": round(self.last_ms)}


class BluetoothMonitor(threading.Thread):
    """Runs the long-lived Bluetooth watcher; its snapshots go straight to State.

    The *fallback* one-shot polling is driven by the collector's 1 Hz scheduler,
    not by this thread: the old loop polled every 2 s whenever the watcher was
    missing (``poll_interval`` was stored but never used), which would blow the
    P3 budget with a PowerShell process (audit P1-3).
    """

    def __init__(self, provider: Any,
                 on_snapshot: Callable[[List[DeviceStatus]], None],
                 stop_event: threading.Event, poll_interval: float = 60.0):
        super().__init__(name="collector-bluetooth", daemon=True)
        self.provider = provider
        self.on_snapshot = on_snapshot
        self.stop_event = stop_event
        self.poll_interval = poll_interval
        self.watcher_alive = False
        self.snapshots = 0
        self.fallback_polls = 0
        self._watcher: Any = None

    def run(self) -> None:
        try:
            from .providers.bluetooth import BluetoothWatcher
            self._watcher = BluetoothWatcher(self.provider, self._update).start()
            self.watcher_alive = True
        except Exception:
            log.exception("bluetooth watcher could not start")
            self.watcher_alive = False
            return
        # Wait for shutdown (or for the collector to notice a failed watcher and
        # flip to the scheduler-driven fallback). No periodic work here.
        self.stop_event.wait()
        self.watcher_alive = False
        watcher = self._watcher
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass

    def healthy(self) -> bool:
        watcher = self._watcher
        if watcher is not None and getattr(watcher, "failed", False):
            self.watcher_alive = False
        return self.watcher_alive

    def poll_once(self) -> List[DeviceStatus]:
        """One-shot fallback poll (called from a daemon worker, not the scheduler)."""
        self.fallback_polls += 1
        try:
            devices = self.provider.poll()
        except Exception:
            log.exception("bluetooth fallback poll")
            devices = []
        self._update(devices)
        return devices

    def _update(self, devices: List[DeviceStatus]) -> None:
        self.snapshots += 1
        try:
            self.on_snapshot(list(devices or []))
        except Exception:
            log.exception("bluetooth snapshot")

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the watcher and make sure its PowerShell child is really gone."""
        self.stop_event.set()
        self.join(timeout)
        watcher = self._watcher
        if watcher is None:
            self.watcher_alive = False
            return
        try:
            watcher.stop()                      # sets the watcher's stop flag
        except Exception:
            log.exception("bluetooth watcher stop")
        proc = getattr(watcher, "_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=0.5)
            except Exception:                   # still alive: force it
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    log.warning("bluetooth watcher PowerShell did not exit")
        self.watcher_alive = False


class Collector:
    """Owns the provider pool, the scheduler thread and the command handling."""

    def __init__(self, providers: Iterable[Any], settings: Any, protocol: Any,
                 state: StateStore, keys: Any,
                 bluetooth_provider: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], float] = time.time,
                 tick_period: Optional[float] = None,
                 fake_world: Any = None):
        self.settings = settings
        self.protocol = protocol
        self.state = state
        self.keys = keys
        self.clock = clock
        self.wall = wallclock
        self.fake_world = fake_world
        self.bluetooth_provider = bluetooth_provider
        self.tasks: Dict[str, ProviderTask] = {}
        for provider in providers:
            name = getattr(provider, "name", "")
            self.tasks[name] = ProviderTask(provider, self._interval_for_name(name), clock)
        # A fake/demo run ticks faster in real time because its clock is scaled;
        # production keeps exactly 1 wake-up per second (P8).
        self.tick_period = tick_period if tick_period is not None else 1.0
        self._pool = DaemonPool(max(1, int(settings.get("threads", 4))))
        self._stop = threading.Event()
        self._wake_all = False
        self._woken: Set[str] = set()
        self._lock = threading.RLock()
        self._scheduler: Optional[threading.Thread] = None
        self._bt_monitor: Optional[BluetoothMonitor] = None
        self._started = False
        self._wake_times: List[float] = []
        self._tick_count = 0
        self._started_at = 0.0
        self._round_active = False
        self._round_started = 0.0
        self._round_timeout = 90.0
        # number of provider polls executed because a refresh/wake asked for them
        # (observability for the "refresh during a round" regression, P2)
        self.fast_polls = 0
        # scheduler-driven Bluetooth fallback (audit P1-3): one poll per
        # poll_interval only, never every 2 s.
        self._bt_next_poll = 0.0
        self._bt_poll_in_flight = False
        self._bt_poll_polls = 0
        self._bt_poll_ms = 0.0
        self._bt_poll_last_ms = 0.0
        self._bt_health_checked = 0.0
        # Effective Bluetooth watcher connection-poll interval (ms); None in fake
        # runs. Exposed in stat for the hidden-state P2 acceptance measurement.
        self._bt_watch_ms: Optional[int] = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._started_at = time.monotonic()
        if self.fake_world is None:
            # Real run: provider code uses subprocess.run for WGI / Bluetooth
            # one-shot queries, which would otherwise outlive a force-killed
            # collector (audit P2-1 residual). Fake runs never spawn children.
            try:
                from . import procs
                procs.install()
            except Exception:
                log.exception("child process registry could not be installed")
        if self.bluetooth_provider is not None and self.settings.is_enabled("bluetooth"):
            try:
                # Patch only the in-memory PowerShell text: the upstream
                # providers/bluetooth.py file stays byte-identical. The connection
                # state is read every bt_watch_ms (30 s instead of upstream 2 s is
                # the accepted P2 trade-off); battery snapshots wake at the
                # earliest due time, so the +0/+3/+8/+15 s re-reads after a
                # connection change and the +3/+8/+15/+30 s cold-start retries
                # really fire instead of collapsing into the poll interval
                # (review P1-11).
                from . import btconfig
                self._bt_watch_ms = btconfig.configure(self.settings)
            except Exception:
                log.exception("could not configure the bluetooth watcher interval")
                self._bt_watch_ms = None
            self._bt_monitor = BluetoothMonitor(
                self.bluetooth_provider, self._on_bluetooth, self._stop)
            self._bt_monitor.start()
        self._scheduler = threading.Thread(target=self._scheduler_loop,
                                           name="collector-scheduler", daemon=True)
        self._scheduler.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._lock:
            self._wake_all = True
        if self._bt_monitor is not None:
            self._bt_monitor.stop(timeout=1.0)
        if self._scheduler is not None:
            self._scheduler.join(timeout)
        self._pool.close(0.3)
        if self.fake_world is None:
            try:
                from . import procs
                killed = procs.terminate_all(0.5)
                if killed:
                    log.info("terminated %d child process(es) on shutdown", killed)
            except Exception:
                log.exception("could not terminate child processes")
        self.keys.save(force=True)

    def _on_bluetooth(self, devices: List[DeviceStatus]) -> None:
        self.state.on_bluetooth_snapshot(devices)

    # ------------------------------------------------------------ scheduling
    def _interval_for_name(self, name: str) -> float:
        try:
            return float(self.settings.provider_interval(name))
        except Exception:
            return 15.0

    def _interval_after_poll(self, task: ProviderTask, devices: List[DeviceStatus]) -> float:
        interval = self._interval_for_name(task.name)
        if task.name == "steam" and devices:
            interval = min(interval, 8.0)      # Steam: 8 s while a controller is there
        if getattr(task.provider, "pending", False):
            interval = min(interval, 3.0)      # provider asks for a fast re-check
        return max(1.0, interval)

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._scheduler_wake()
            except Exception:
                log.exception("scheduler")
            # virtual-clock runs wake faster in real time so scenario seconds pass
            elapsed = time.monotonic() - started
            delay = max(0.0, self.tick_period - elapsed)
            if delay > 0:
                self._stop.wait(delay)
            self._record_wake()

    def _record_wake(self) -> None:
        now = time.monotonic()
        self._wake_times.append(now)
        cutoff = now - 10.0
        while self._wake_times and self._wake_times[0] < cutoff:
            self._wake_times.pop(0)
        self._tick_count += 1

    def _scheduler_wake(self) -> None:
        self._reap_stuck_tasks()
        with self._lock:
            now = self.clock()
            due: List[ProviderTask] = []
            round_active = self._round_active
            round_started = self._round_started
            if not round_active:
                # Only a new round consumes wake flags. A refresh that arrives
                # while a round is in flight stays queued and forces the next
                # round (BUILD_PLAN P2: "refresh during round must not be lost").
                wake_all = self._wake_all
                self._wake_all = False
                woken = set(self._woken)
                self._woken.clear()
                capacity = self._pool.free_slots
                for task in self.tasks.values():
                    if capacity <= 0:
                        break
                    if task.in_flight:
                        continue
                    forced = wake_all or task.name in woken
                    if forced or now >= task.next_due:
                        task.in_flight = True
                        task.started_mono = time.monotonic()
                        due.append(task)
                        capacity -= 1
                        if forced:
                            self.fast_polls += 1
                if due:
                    round_active = True
                    self._round_active = True
                    self._round_started = now
                    round_started = now
            in_flight = {t.name for t in self.tasks.values() if t.in_flight}
        if due:
            # The gate opens with exactly the providers polled in this round; the
            # snapshot keeps the last result of providers that are not due, so the
            # merge helpers still see one complete device set. Submitting after
            # begin_round cannot race: a worker that finishes immediately blocks on
            # the StateStore lock until the gate exists.
            self.state.begin_round([task.name for task in due])
            for task in due:
                if not self._pool.submit(self._run_task, task):
                    with self._lock:
                        task.in_flight = False
                        task.started_mono = 0.0
            in_flight = {t.name for t in self.tasks.values() if t.in_flight}
        self.state.set_in_flight(in_flight)
        # the 1 Hz global tick: gone decisions and the periodic full alignment
        self.state.on_tick()
        self._maybe_emit_stat()
        self._maybe_poll_bluetooth()
        if round_active and (self.clock() - round_started) > self._round_timeout:
            self.state.force_merge()
            with self._lock:
                self._round_active = False

    def _reap_stuck_tasks(self) -> None:
        """Watchdog (audit P2-4): a poll that never returns must not freeze a device.

        The worker thread cannot be killed, but its task is marked not in-flight:
        StateStore may call its devices gone after the TTL, the scheduler retries
        the provider when a worker is free again, and the pool's busy accounting
        stops new work from piling up behind the stuck worker.
        """
        now = time.monotonic()
        reaped: List[ProviderTask] = []
        with self._lock:
            for task in self.tasks.values():
                if (task.in_flight and task.started_mono
                        and now - task.started_mono > task.timeout):
                    task.in_flight = False
                    task.started_mono = 0.0
                    task.timeouts += 1
                    reaped.append(task)
            in_flight = {t.name for t in self.tasks.values() if t.in_flight}
        for task in reaped:
            log.warning("provider %s blocked for more than %.0f s; reaping the poll "
                        "(timeouts=%d)", task.name, task.timeout, task.timeouts)
        if reaped:
            self.state.set_in_flight(in_flight)

    def _maybe_poll_bluetooth(self) -> None:
        """Scheduler-driven Bluetooth fallback, at most once per poll_interval.

        Audit P1-3: the old monitor loop ran a one-shot PowerShell poll every 2 s
        when the watcher was unavailable (``poll_interval`` was never used).
        """
        monitor = self._bt_monitor
        if monitor is None or self._bt_poll_in_flight:
            return
        try:
            if monitor.healthy():
                return
        except Exception:
            log.exception("bluetooth monitor health")
            return
        now = time.monotonic()
        if now < self._bt_next_poll:
            return
        if self._pool.free_slots <= 0:
            return
        if self._pool.submit(self._poll_bluetooth, monitor):
            self._bt_poll_in_flight = True
            self._bt_next_poll = now + max(1.0, float(monitor.poll_interval))

    def _poll_bluetooth(self, monitor: BluetoothMonitor) -> None:
        started = time.perf_counter()
        try:
            monitor.poll_once()
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            with self._lock:
                self._bt_poll_in_flight = False
                self._bt_poll_polls += 1
                self._bt_poll_ms += elapsed
                self._bt_poll_last_ms = elapsed

    def _run_task(self, task: ProviderTask) -> None:
        started = self.clock()
        real_started = time.perf_counter()
        devices: List[DeviceStatus] = []
        try:
            result = task.provider.poll()
            devices = list(result or [])
        except Exception:
            task.errors += 1
            log.exception("provider %s", task.name)
        duration_ms = (time.perf_counter() - real_started) * 1000.0
        with self._lock:
            task.polls += 1
            task.total_ms += duration_ms
            task.last_ms = duration_ms
            task.last_ok = started
            task.interval = self._interval_after_poll(task, devices)
            task.next_due = self.clock() + task.interval
            task.in_flight = False
            task.started_mono = 0.0
            in_flight = {t.name for t in self.tasks.values() if t.in_flight}
        self.state.set_in_flight(in_flight)
        try:
            merged = self.state.on_provider_result(task.name, devices)
        except Exception:
            log.exception("state result for %s", task.name)
            merged = False
        if merged:
            with self._lock:
                self._round_active = False

    # ------------------------------------------------------------ TTL policy
    def ttl_for(self, provider: str, devices: List[DeviceStatus]) -> float:
        """StateStore's TTL lookup. Bluetooth snapshots are sparse (the watcher
        emits a full one per minute), so its TTL has to cover the gap; fake runs
        use the documented per-kind TTLs unchanged."""
        if provider == "bluetooth" and self.fake_world is None:
            monitor = self._bt_monitor
            if monitor is not None and monitor.healthy():
                return 60.0            # threshold 120 s > 60 s snapshot cadence
            return 90.0                # threshold 180 s for the 60 s fallback poll
        try:
            return float(self.settings.ttl_for_devices(provider, devices))
        except Exception:
            return 15.0

    # ------------------------------------------------------------ commands
    def wake(self, provider: Optional[str] = None) -> None:
        """Refresh request.

        * No round open: remember the request; the next scheduler wake polls.
        * A round is open: the request must not be lost (audit/F4). Providers that
          already finished this round are re-polled *now* (the protocol says
          refresh polls immediately), their results update the snapshot without
          closing the round, and the wake flag additionally forces a full round
          once the blocked provider returns.
        """
        submitted: List[ProviderTask] = []
        with self._lock:
            if provider is None:
                self._wake_all = True
                names: Optional[Set[str]] = None
            else:
                self._woken.add(provider)
                names = {provider}
            if self._round_active:
                for task in self.tasks.values():
                    if task.in_flight or (names is not None and task.name not in names):
                        continue
                    if self._pool.free_slots <= 0:
                        break
                    task.in_flight = True
                    task.started_mono = time.monotonic()
                    self.fast_polls += 1
                    submitted.append(task)
            in_flight = {t.name for t in self.tasks.values() if t.in_flight}
        for task in submitted:
            if not self._pool.submit(self._run_task, task):
                with self._lock:
                    task.in_flight = False
                    task.started_mono = 0.0
                    self.fast_polls = max(0, self.fast_polls - 1)
        if submitted:
            self.state.set_in_flight(in_flight)

    def stat_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {name: task.stats() for name, task in self.tasks.items()}
        for task in self.tasks.values():
            if task.timeouts:
                payload[task.name]["timeouts"] = task.timeouts
        monitor = self._bt_monitor
        if monitor is not None:
            try:
                healthy = monitor.healthy()
            except Exception:
                healthy = False
            payload["bluetooth"] = {
                "polls": self._bt_poll_polls,
                "avg_ms": round(self._bt_poll_ms / self._bt_poll_polls)
                if self._bt_poll_polls else 0,
                "last_ms": round(self._bt_poll_last_ms),
                "watcher": healthy,
                "snapshots": monitor.snapshots,
                "fallback_polls": monitor.fallback_polls,
                "watch_ms": self._bt_watch_ms,
            }
        return payload

    def timers_per_second(self) -> float:
        times = list(self._wake_times)
        if len(times) < 2:
            return round(1.0 / self.tick_period, 3) if self.tick_period else 1.0
        span = times[-1] - times[0]
        return round((len(times) - 1) / span, 3) if span > 0 else 0.0

    def _maybe_emit_stat(self) -> None:
        now = self.clock()
        interval = float(self.settings.stat_interval)
        last = getattr(self, "_last_stat", None)
        if last is None:
            self._last_stat = now
            return
        if now - last < interval:
            return
        self._last_stat = now
        self.protocol.stat(self.stat_payload(), self.timers_per_second(),
                           self.keys.migration_count)

    # ------------------------------------------------------------ commands
    def handle_command(self, msg: Dict[str, Any]) -> bool:
        """Apply one stdin command. Returns False when the process must exit."""
        kind = msg.get("t")
        if kind == "shutdown":
            return False
        if kind == "refresh":
            provider = msg.get("provider")
            self.wake(provider if isinstance(provider, str) else None)
            self.state.request_tick()
            self.state.on_tick()
            return True
        if kind == "get":
            if msg.get("what") == "snapshot":
                self.state.request_tick()
                self.state.on_tick()
            return True
        if kind == "set":
            key = msg.get("key")
            field = msg.get("field")
            if isinstance(key, str) and isinstance(field, str):
                if self.settings.apply_set(key, field, msg.get("value")):
                    self.state.refresh_from_settings()
                    self.state.on_tick()
                else:
                    log.warning("set: invalid command %r", msg)
            return True
        log.warning("unknown command: %r", kind)
        return True

    # ------------------------------------------------------------ run modes
    def wait_until_ready(self, timeout: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state.coverage_complete:
                return True
            if self._stop.is_set():
                return False
            time.sleep(0.01 if self.fake_world is not None else 0.05)
        return self.state.coverage_complete

    def wait_for_bluetooth(self, timeout: float = 4.0) -> bool:
        """Wait for the Bluetooth watcher's first complete snapshot.

        Bluetooth is an event provider, so it is not part of the HID ready gate;
        ``--once``/``--probe`` would otherwise finish in ~0.2 s with no Bluetooth
        device at all (lead audit F5). Bounded, because a failing watcher falls
        back to a slow one-shot poll.
        """
        if self._bt_monitor is None:
            return False
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if self.state.bluetooth_snapshots:
                return True
            if self._stop.is_set():
                return False
            time.sleep(0.05)
        return bool(self.state.bluetooth_snapshots)

    def wait_until_settled(self, timeout: float = 2.0) -> bool:
        """Wait for an open poll round to finish (e.g. after a BT snapshot)."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if not self.state.pending:
                return True
            if self._stop.is_set():
                return False
            time.sleep(0.02)
        return not self.state.pending

    def run_once(self, timeout: float = 90.0,
                 bluetooth_timeout: float = 4.0) -> bool:
        """One complete snapshot round, no stdin loop (--once / --probe)."""
        self.start()
        try:
            ok = self.wait_until_ready(timeout)
            if ok and self._bt_monitor is not None:
                self.wait_for_bluetooth(bluetooth_timeout)
                self.wait_until_settled(2.0)
            return ok
        finally:
            self.stop()

    def run_for(self, seconds: float, ready_timeout: float = 90.0,
                bluetooth_timeout: float = 4.0) -> bool:
        """Run the collector for a bounded time, then stop (real --probe)."""
        self.start()
        started = time.monotonic()
        try:
            ok = self.wait_until_ready(ready_timeout)
            if self._bt_monitor is not None and not self._stop.is_set():
                self.wait_for_bluetooth(bluetooth_timeout)
            deadline = started + max(0.0, float(seconds))
            while not self._stop.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)
            return ok
        finally:
            self.stop()

    def serve(self, commands: Iterable[dict]) -> None:
        """Long-running stdio mode: hello, scheduler, then one command loop."""
        self.protocol.hello([], self.state.settings_payload())
        self.start()
        try:
            for message in commands:
                if not self.handle_command(message):
                    break
            # stdin reached EOF: the app that owns us is gone, so exit instead of
            # lingering as an orphan (P10).
        finally:
            self.stop()

    def serve_until(self, predicate: Callable[[], bool],
                    commands: Iterable[dict] = (), timeout: float = 120.0) -> None:
        """Run until ``predicate`` is true (used by the demo/probe mode)."""
        self.protocol.hello([], self.state.settings_payload())
        self.start()
        deadline = time.monotonic() + timeout
        command_iter = iter(commands)
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                if predicate():
                    break
                try:
                    message = next(command_iter)
                except StopIteration:
                    message = None
                if message is not None and not self.handle_command(message):
                    break
                time.sleep(0.01 if self.fake_world is not None else 0.1)
        finally:
            self.stop()


# ---------------------------------------------------------------- factories
def make_providers(jbl_listen_first: float = 0.0) -> List[Any]:
    """The upstream provider list (hb/halo_battery.pyw make_providers).

    ``providers/`` is copied unmodified; this factory is the collector's own
    minimal extraction so the upstream app's tray/UI code is not dragged in.
    """
    from .providers import (
        AmInfinityProvider, AstroProvider, AsusProvider, AudezeProvider,
        BarracudaProvider, CorsairProvider, EightBitDoProvider, GWolvesProvider,
        HyperXAlpha2Provider, HyperXCloud3Provider, HyperXCloud3SProvider,
        HyperXProvider, JblProvider, KeychronProvider, LamzuProvider,
        LofreeProvider, LogitechProvider, LogitechCenturionProvider,
        LunafuryProvider, MchoseProvider, NintendoProvider, NzxtProvider,
        PlayStationProvider, PulsarProvider, RazerProvider,
        SteelSeriesEliteProvider, SteelSeriesProvider, WLmouseProvider,
        XInputProvider, SteamProvider,
    )
    return [RazerProvider(), AudezeProvider(), WLmouseProvider(), MchoseProvider(),
            HyperXAlpha2Provider(), HyperXCloud3Provider(), HyperXProvider(),
            KeychronProvider(), PulsarProvider(),
            JblProvider(listen_first=jbl_listen_first), LogitechProvider(),
            SteelSeriesProvider(), XInputProvider(),
            PlayStationProvider(), EightBitDoProvider(), BarracudaProvider(),
            NintendoProvider(), AsusProvider(), GWolvesProvider(), LofreeProvider(),
            AstroProvider(), CorsairProvider(),
            LamzuProvider(), LunafuryProvider(), AmInfinityProvider(),
            SteelSeriesEliteProvider(), LogitechCenturionProvider(),
            HyperXCloud3SProvider(), NzxtProvider(),
            SteamProvider()]


def make_bluetooth_provider() -> Any:
    from .providers.bluetooth import BluetoothProvider
    return BluetoothProvider()
