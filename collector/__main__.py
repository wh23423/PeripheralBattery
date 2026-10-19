"""Command line entry point: ``python -m collector``.

Modes (BUILD_PLAN §9-M0):

    --stdio            default; NDJSON events on stdout, commands on stdin
    --probe            human-readable event stream (--fake) or one real poll
    --once             one complete snapshot as NDJSON, then exit
    --fake             deterministic fake devices (--scenario demo|static|migration)
    --diag             diagnostics report (provider protocol details)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .collector import Collector, make_bluetooth_provider, make_providers
from .fake import FakeWorld, make_world
from .history import History
from .keys import KeyStore
from .protocol import Protocol
from .settings import Settings, build_string
from .state import StateStore
from .transport import (LineWriter, close_stderr_log, ensure_utf8_stdio,
                        install_stderr_log, read_commands)

log = logging.getLogger("halo_battery")


# ---------------------------------------------------------------- probe output
class ProbeWriter:
    """Human-readable rendering of the same messages the NDJSON writer sends."""

    def __init__(self, clock=None):
        self._clock = clock
        self._start = time.monotonic()
        self.sent = 0

    def _t(self) -> str:
        if self._clock is not None:
            return f"t={self._clock():7.2f}s"
        return f"t={time.monotonic() - self._start:7.2f}s"

    @staticmethod
    def _fmt_device(device: dict) -> str:
        level = "未知" if device.get("level") is None else f"{device['level']}%"
        parts = [f"{device.get('key')}", f'"{device.get("name")}"', f"level={level}"]
        if device.get("approx"):
            parts.append(f"approx={device['approx']!r}")
        if device.get("left_s") is not None:
            parts.append(f"left_s={device['left_s']}")
        parts.append(f"charging={device.get('charging')}")
        parts.append(f"online={device.get('online')}")
        parts.append(f"kind={device.get('kind')}")
        parts.append(f"transport={device.get('transport')}")
        if device.get("via"):
            parts.append(f"via={device['via']}")
        return " ".join(parts)

    def send(self, message: Dict[str, Any]) -> bool:
        self.sent += 1
        kind = message.get("t")
        seq = message.get("seq")
        head = f"[{self._t()}] {kind:<6} seq={seq}"
        if kind == "hello":
            print(f"[{self._t()}] hello  session={message.get('session', '')[:8]} "
                  f"build={message.get('build')} "
                  f"capabilities={','.join(message.get('capabilities') or [])} "
                  f"devices={len(message.get('devices') or [])}")
        elif kind == "device":
            print(f"{head} {self._fmt_device(message.get('device') or {})}"
                  + (f"  REPLACES {message['replaces']}" if message.get("replaces") else ""))
        elif kind == "gone":
            print(f"{head} key={message.get('key')}")
        elif kind == "tick":
            devices = message.get("devices") or []
            print(f"{head} devices={len(devices)}")
            for device in devices:
                print(f"           - {self._fmt_device(device)}")
        elif kind == "alert":
            print(f"{head} {message.get('kind')} key={message.get('key')} "
                  f"level={message.get('level')} text={message.get('text')!r}")
        elif kind == "stat":
            providers = message.get("providers") or {}
            print(f"{head} timers_per_s={message.get('timers_per_s')} "
                  f"key_migrations={message.get('key_migrations')} "
                  f"providers={len(providers)}")
        else:
            print(f"{head} {message}")
        return True


class NullWriter:
    """Swallows events while --once collects its single snapshot."""

    def send(self, message: Dict[str, Any]) -> bool:
        return True


# ---------------------------------------------------------------- helpers
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m collector",
        description="Peripheral battery collector (M0)")
    parser.add_argument("--stdio", action="store_true",
                        help="NDJSON on stdout (default)")
    parser.add_argument("--probe", action="store_true", help="human-readable event stream")
    parser.add_argument("--once", action="store_true", help="one snapshot then exit")
    parser.add_argument("--fake", action="store_true", help="deterministic fake devices")
    parser.add_argument("--scenario", default="demo",
                        choices=("demo", "static", "migration", "slow"),
                        help="fake scenario (default demo)")
    parser.add_argument("--speed", type=float, default=6.0,
                        help="fake virtual clock speed (default 6)")
    parser.add_argument("--duration", type=float, default=None,
                        help="fake: run length in virtual seconds; real --probe: "
                             "run length in seconds (default 5)")
    parser.add_argument("--diag", action="store_true", help="write a diagnostics report")
    parser.add_argument("--data-dir", default=None,
                        help="override %%LOCALAPPDATA%%\\PeripheralBattery")
    parser.add_argument("--build", default=None, help="build id for hello")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def prepare_settings(args: argparse.Namespace) -> Settings:
    """Create the data directory. Fake runs default to a throw-away one so the
    generated keys are deterministic and the real app data is not touched."""
    data_dir = args.data_dir
    if args.fake and not data_dir and not os.environ.get("PB_DATA_DIR"):
        data_dir = tempfile.mkdtemp(prefix="pb-collector-fake-")
    settings = Settings(data_dir)
    settings.ensure_dirs()
    return settings


def build_runtime(args: argparse.Namespace, writer: Any,
                  settings: Settings) -> Tuple[Collector, StateStore, KeyStore,
                                               History, Optional[FakeWorld]]:
    world: Optional[FakeWorld] = None
    bluetooth_provider = None

    if args.fake:
        world = make_world(args.scenario, args.speed)
        providers = list(world.providers)
        event_providers: Tuple[str, ...] = ()
        state_names = [p.name for p in providers if settings.is_enabled(p.name)]
        clock, wall = world.mono, world.wall
        tick_period = max(0.005, 1.0 / max(1.0, args.speed))
    else:
        providers = [p for p in make_providers() if settings.is_enabled(p.name)]
        event_providers = ("bluetooth",)
        state_names = [p.name for p in providers]
        if settings.is_enabled("bluetooth"):
            bluetooth_provider = make_bluetooth_provider()
            state_names.append("bluetooth")
        clock, wall = time.monotonic, time.time
        tick_period = max(0.2, 1.0 / max(0.2, float(settings.tick_hz)))

    history = History(settings.history_path)
    history.load()
    keys = KeyStore(settings.aliases_path)
    protocol = Protocol(writer, build=args.build or build_string())
    state = StateStore(
        providers=state_names, sink=protocol, keys=keys, settings=settings,
        hints_for=world.hints_for if world is not None else None,
        history=history, event_providers=event_providers,
        clock=clock, wallclock=wall)
    collector = Collector(
        providers=providers, settings=settings, protocol=protocol, state=state,
        keys=keys, bluetooth_provider=bluetooth_provider,
        clock=clock, wallclock=wall, tick_period=tick_period,
        fake_world=world)
    state.set_ttl_for(collector.ttl_for)
    return collector, state, keys, history, world


def print_summary(collector: Collector, state: StateStore, keys: KeyStore,
                  settings: Settings) -> None:
    print("\n=== Summary ===")
    devices = state.snapshot_devices()
    if devices:
        for device in devices:
            print(f"  {ProbeWriter._fmt_device(device)}")
    else:
        print("  (no devices)")
    stats = state.stats()
    print("  provider polls: "
          + ", ".join(f"{name}={task.polls}" for name, task in collector.tasks.items()))
    print(f"  device events: {stats['device_events']}, ticks: {stats['tick_events']}, "
          f"gone: {stats['gone']}, key migrations: {keys.migration_count}")
    for entry in keys.history():
        print(f"  migration {entry.get('from')} -> {entry.get('to')} "
              f"({entry.get('reason')}, legacy={entry.get('legacy')})")
    print(f"  data dir: {settings.data_dir}")
    if collector.tasks:
        print("  diagnostics:")
    for task in collector.tasks.values():
        try:
            for line in task.provider.diagnostics():
                print(f"    {line}")
        except Exception as exc:              # diagnostics must never break the run
            print(f"    ({task.name}: {exc})")


# ---------------------------------------------------------------- modes
def run_probe(args: argparse.Namespace) -> int:
    ensure_utf8_stdio()
    settings = prepare_settings(args)
    probe = ProbeWriter()
    collector, state, keys, history, world = build_runtime(args, probe, settings)
    probe._clock = world.elapsed if world is not None else None
    if world is not None:
        duration = args.duration if args.duration is not None else world.duration
        collector.serve_until(lambda: world.elapsed() >= duration,
                              timeout=max(30.0, duration / max(0.1, args.speed) + 60.0))
    else:
        # Real --probe: run for a bounded time (default 5 s) so the Bluetooth
        # watcher has time to deliver its first snapshot (lead audit F5).
        duration = args.duration if args.duration is not None else 5.0
        collector.protocol.hello([], state.settings_payload())
        collector.run_for(duration)
    print_summary(collector, state, keys, settings)
    keys.save(force=True)
    history.save(force=True)
    return 0


def run_once(args: argparse.Namespace) -> int:
    ensure_utf8_stdio()
    settings = prepare_settings(args)
    writer = LineWriter()
    collector, state, keys, history, world = build_runtime(args, NullWriter(), settings)
    ok = collector.run_once(timeout=120.0)
    collector.protocol.writer = writer
    collector.protocol.reset_seq()
    devices = state.snapshot_devices()
    collector.protocol.hello(devices, state.settings_payload())
    collector.protocol.tick(devices)
    keys.save(force=True)
    history.save(force=True)
    if not ok:
        log.error("no complete snapshot within the timeout")
        return 2
    return 0


def run_stdio(args: argparse.Namespace) -> int:
    settings = prepare_settings(args)
    ensure_utf8_stdio()
    stderr_log = install_stderr_log(settings.log_path)
    log.info("collector %s starting (pid %d, data dir %s)", __version__,
             os.getpid(), settings.data_dir)
    try:
        collector, state, keys, history, world = build_runtime(args, LineWriter(), settings)
        collector.serve(read_commands())
        keys.save(force=True)
        history.save(force=True)
    finally:
        close_stderr_log(stderr_log)
    return 0


def run_diag(args: argparse.Namespace) -> int:
    ensure_utf8_stdio()
    settings = prepare_settings(args)
    lines = [f"PeripheralBattery collector {__version__}  "
             f"{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Python {sys.version.split()[0]}  {sys.platform}",
             f"data folder: {settings.data_dir}", ""]
    world = make_world(args.scenario, args.speed) if args.fake else None
    providers = list(world.providers) if world is not None else make_providers()
    if not args.fake and settings.is_enabled("bluetooth"):
        providers.append(make_bluetooth_provider())
    keys = KeyStore(settings.aliases_path)
    lines.append("=== Providers ===")
    for provider in providers:
        if not settings.is_enabled(getattr(provider, "name", "")):
            continue
        try:
            found = provider.poll()
        except Exception as exc:
            lines.append(f"[{provider.name}] poll failed: {exc}")
            continue
        lines.append(f"[{provider.name}] {len(found)} device(s)")
        try:
            lines += ["  " + line for line in provider.diagnostics()]
        except Exception as exc:
            lines.append(f"  (diagnostics failed: {exc})")
    lines.append("")
    lines.append("=== Key aliases ===")
    for legacy, entry in sorted(keys.aliases().items()):
        lines.append(f"  {legacy} -> {entry.get('stable')}")
    lines.append(f"  migrations: {keys.migration_count}")
    for entry in keys.history():
        lines.append(f"  {entry}")
    lines.append("")
    lines.append("=== Config ===")
    for key in ("poll", "providers", "bluetooth", "low", "tick_interval",
                "stat_interval", "tick_hz", "threads"):
        lines.append(f"  {key}: {settings.get(key)!r}")
    try:
        from .providers import hidlist
        lines += ["", "=== HID list ===", "  " + repr(hidlist.stats())]
    except Exception:
        pass
    text = "\n".join(lines)
    path = os.path.join(settings.diagnostics_dir, "collector-diag.txt")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return 1
    print(f"diagnostics written to {path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.probe:
        return run_probe(args)
    if args.diag:
        return run_diag(args)
    if args.once:
        return run_once(args)
    return run_stdio(args)


if __name__ == "__main__":
    raise SystemExit(main())
