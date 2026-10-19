"""stdio hardening: line protocol, length caps, stderr thread, real subprocesses."""
from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from conftest import ROOT

from collector import transport
from collector.transport import (LineWriter, QueueStream, RotatingLog,
                                 ensure_utf8_stdio, install_stderr_log,
                                 close_stderr_log, read_commands)


class SpyStream:
    def __init__(self):
        self.data = ""
        self.flushes = 0

    def write(self, text):
        self.data += text
        return len(text)

    def flush(self):
        self.flushes += 1


# ---------------------------------------------------------------- line writer
def test_line_writer_one_utf8_line_per_message_flushed():
    stream = SpyStream()
    writer = LineWriter(stream)
    assert writer.send({"t": "device", "name": "客厅手柄", "level": None})
    text = stream.data
    assert text.endswith("\n") and text.count("\n") == 1
    assert "\r" not in text
    assert not text.startswith("\ufeff")
    assert "客厅手柄" in text                       # not \\u escaped
    message = json.loads(text)
    assert message["level"] is None
    assert stream.flushes == 1


def test_line_writer_drops_over_long_lines():
    stream = SpyStream()
    writer = LineWriter(stream, max_line=40)
    assert writer.send({"name": "x" * 200}) is False
    assert stream.data == ""
    assert writer.dropped == 1


# ---------------------------------------------------------------- command reader
def test_read_commands_parses_json_lines_and_skips_junk():
    raw = (b'{"t":"refresh"}\n'
           b"\n"
           b"not json\n"
           b"[1,2]\n"
           b'{"t":"get","what":"snapshot"}\n')
    messages = list(read_commands(io.BytesIO(raw)))
    assert [m.get("t") for m in messages] == ["refresh", "get"]


def test_read_commands_drops_over_long_line_and_resynchronises():
    raw = (b'{"t":"refresh"}\n'
           + b'{"pad":"' + b"x" * 500 + b'"}\n'
           + b'{"t":"shutdown"}\n')
    messages = list(read_commands(io.BytesIO(raw), max_line=64))
    assert [m["t"] for m in messages] == ["refresh", "shutdown"]


def test_read_commands_discards_partial_line_at_eof():
    assert list(read_commands(io.BytesIO(b'{"t":"shutdown"}'))) == []
    assert [m["t"] for m in read_commands(
        io.BytesIO(b'{"t":"refresh"}\n{"t":"shutdown"}'))] == ["refresh"]


def test_read_commands_replaces_invalid_utf8_instead_of_crashing():
    messages = list(read_commands(io.BytesIO(b'{"t":"refresh","x":"\xff"}\n')))
    assert len(messages) == 1
    assert messages[0]["t"] == "refresh"


def test_read_commands_multibyte_overlong_line_resynchronises():
    """Audit P3-1: length must be judged in BYTES, not decoded characters.

    A >cap CJK line decodes to fewer characters than the byte cap; the old check
    mistook it for an EOF fragment and killed the whole stdin stream.
    """
    overlong = ('{"pad":"' + "中" * 40 + '"}').encode("utf-8")
    assert len(overlong) > 64 and len(overlong.decode("utf-8")) <= 64
    raw = overlong + b"\n" + b'{"t":"shutdown"}\n'
    messages = list(read_commands(io.BytesIO(raw), max_line=64))
    assert [m["t"] for m in messages] == ["shutdown"]


# ---------------------------------------------------------------- utf-8 setup
def test_ensure_utf8_stdio_reopens_missing_streams(monkeypatch):
    saved_out, saved_err = sys.stdout, sys.stderr
    fake = {1: io.StringIO(), 2: io.StringIO()}
    monkeypatch.setattr(transport.os, "fdopen",
                        lambda fd, *a, **k: fake[fd])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        ensure_utf8_stdio()
        assert sys.stdout is fake[1]
        assert sys.stderr is fake[2]
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err


def test_ensure_utf8_stdio_reconfigures_existing_streams(monkeypatch):
    class Recorder:
        def __init__(self):
            self.kwargs = None

        def reconfigure(self, **kwargs):
            self.kwargs = kwargs

    out, err = Recorder(), Recorder()
    saved_out, saved_err = sys.stdout, sys.stderr
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        ensure_utf8_stdio()
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    for stream in (out, err):
        assert stream.kwargs["encoding"] == "utf-8"
        assert stream.kwargs["line_buffering"] is True
        assert stream.kwargs["newline"] == "\n"


# ---------------------------------------------------------------- stderr thread
def test_stderr_thread_writes_rotating_log(tmp_path):
    path = tmp_path / "logs" / "collector.log"
    saved = sys.stderr
    writer = install_stderr_log(str(path))
    try:
        sys.stderr.write("第一行 UTF-8\n")
        ipv6 = "::1"                                # keep linters quiet about writes
        sys.stderr.write(f"second {ipv6}\n")
    finally:
        close_stderr_log(writer)
        sys.stderr = saved
    text = path.read_text(encoding="utf-8")
    assert "第一行 UTF-8" in text
    assert "second ::1" in text


def test_rotating_log_rotates_when_large(tmp_path):
    path = tmp_path / "collector.log"
    log = RotatingLog(str(path), max_bytes=10, backups=2).start()
    log.write("x" * 40 + "\n")
    log.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "collector.log" in files
    assert any(name.startswith("collector.log.") for name in files)


def test_queue_stream_never_blocks_and_reports_not_a_tty(tmp_path):
    log = RotatingLog(str(tmp_path / "c.log")).start()
    stream = QueueStream(log)
    stream.write("hello")
    stream.flush()
    assert stream.isatty() is False
    log.close()


# ---------------------------------------------------------------- subprocesses
def _env(tmp_path):
    env = dict(os.environ)
    env.pop("PB_DATA_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args, tmp_path, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "collector"] + args +
        ["--data-dir", str(tmp_path / "data")],
        cwd=ROOT, env=_env(tmp_path), capture_output=True, text=True,
        encoding="utf-8", timeout=timeout)


def test_once_fake_produces_valid_ndjson(tmp_path):
    result = _run(["--once", "--fake", "--scenario", "demo", "--speed", "50"],
                  tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout.startswith("\ufeff")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    messages = [json.loads(line) for line in lines]
    assert messages[0]["t"] == "hello"
    assert messages[0]["protocol"] == 1
    assert messages[0]["seq"] == 0
    assert messages[0]["build"]
    assert len(messages[0]["devices"]) == 5
    assert messages[1]["t"] == "tick"
    assert messages[1]["seq"] == 1
    assert all(m.get("t") in ("hello", "device", "gone", "tick", "alert", "stat")
               for m in messages)                       # only protocol events
    kinds = {d["kind"] for d in messages[0]["devices"]}
    assert kinds == {"mouse", "headset", "keyboard", "gamepad", "bluetooth"}
    devices = {d["key"]: d for d in messages[0]["devices"]}
    assert any(d["level"] is None for d in devices.values())
    assert any(d["approx"] for d in devices.values())
    assert any(d["charging"] for d in devices.values())
    assert any(d["name"] == "WH-1000XM5" for d in devices.values())


def test_stdio_bidirectional_line_protocol(tmp_path):
    """Real pipe: hello is flushed at once, a command gets a tick, shutdown exits."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "--stdio", "--fake",
         "--scenario", "static", "--speed", "20",
         "--data-dir", str(tmp_path / "data")],
        cwd=ROOT, env=_env(tmp_path), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", bufsize=1)
    lines: "queue.Queue[str]" = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    stderr_chunks = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()),
                             daemon=True)
    drain.start()

    def next_message(timeout=30):
        line = lines.get(timeout=timeout)
        return json.loads(line)

    def read_until(predicate, timeout=30):
        deadline = __import__("time").monotonic() + timeout
        while True:
            remaining = deadline - __import__("time").monotonic()
            if remaining <= 0:
                raise TimeoutError("condition not reached")
            message = next_message(remaining)
            if predicate(message):
                return message

    try:
        hello = next_message()
        assert hello["t"] == "hello"
        assert "../" not in hello.get("build", "")
        # Wait for the first complete snapshot (five device events), then the
        # explicit snapshot command must answer with all of them.
        seen = set()
        while len(seen) < 5:
            message = next_message()
            if message["t"] == "device":
                seen.add(message["device"]["key"])
        proc.stdin.write('{"t":"get","what":"snapshot"}\n')
        proc.stdin.flush()
        tick = read_until(lambda m: m["t"] == "tick" and len(m["devices"]) == 5)
        assert {d["kind"] for d in tick["devices"]} == {
            "mouse", "headset", "keyboard", "gamepad", "bluetooth"}

        proc.stdin.write('{"t":"refresh"}\n')
        proc.stdin.flush()
        assert read_until(lambda m: m["t"] == "tick")["t"] == "tick"

        proc.stdin.write('{"t":"shutdown"}\n')
        proc.stdin.flush()
        code = proc.wait(timeout=30)
        assert code == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        assert not stderr_chunks or "Traceback" not in "".join(stderr_chunks)


def test_probe_fake_stderr_is_clean_and_emits_devices(tmp_path):
    """--probe keeps stderr on the console: a provider/state traceback must be
    visible there, so the test captures it and fails on any traceback."""
    result = _run(["--probe", "--fake", "--scenario", "demo", "--speed", "20",
                   "--duration", "2"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "NameError" not in result.stderr
    assert "state result for" not in result.stderr
    device_lines = [line for line in result.stdout.splitlines()
                    if "] device " in line]
    assert len(device_lines) >= 4, result.stdout
    assert "=== Summary ===" in result.stdout


def test_stdio_fake_three_seconds_no_traceback_and_at_least_four_devices(tmp_path):
    """End-to-end: the exact mode the app uses, on a real pipe, for 3 seconds.

    A NameError in StateStore._apply used to crash every provider thread and the
    UI saw only `hello` + an empty tick, while the unit tests still passed. This
    test parses *every* stdout line, checks at least four device events, and
    looks for tracebacks both on stderr and in the rotating log file (which is
    where the collector sends the provider threads' tracebacks).
    """
    data_dir = tmp_path / "data"
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "--stdio", "--fake",
         "--scenario", "demo", "--speed", "20", "--data-dir", str(data_dir)],
        cwd=ROOT, env=_env(tmp_path), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", bufsize=1)
    lines: "queue.Queue[str]" = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()
    collected = []
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                collected.append(lines.get(timeout=0.1))
            except queue.Empty:
                continue
        proc.stdin.write('{"t":"shutdown"}\n')
        proc.stdin.flush()
        assert proc.wait(timeout=30) == 0
        while True:
            try:
                collected.append(lines.get_nowait())
            except queue.Empty:
                break
        stderr_text = proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    messages = [json.loads(line) for line in collected if line.strip()]
    assert messages and messages[0]["t"] == "hello"
    assert all(m.get("t") in ("hello", "device", "gone", "tick", "alert", "stat")
               for m in messages)
    device_events = [m for m in messages if m["t"] == "device"]
    assert len(device_events) >= 4, collected[:5]
    kinds = {m["device"]["kind"] for m in device_events}
    assert {"mouse", "headset", "keyboard", "gamepad"} <= kinds

    log_path = data_dir / "logs" / "collector.log"
    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    for text in (stderr_text, log_text):
        assert "Traceback" not in text
        assert "NameError" not in text
        assert "state result for" not in text


def test_probe_fake_is_human_readable_and_shows_replaces(tmp_path):
    result = _run(["--probe", "--fake", "--scenario", "migration",
                   "--speed", "50", "--duration", "8"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "hello" in result.stdout
    assert "REPLACES" in result.stdout
    assert "=== Summary ===" in result.stdout
    assert "key migrations: 1" in result.stdout


def test_diag_writes_a_report(tmp_path):
    result = _run(["--diag", "--fake", "--scenario", "static"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "diagnostics written to" in result.stdout
    report = tmp_path / "data" / "diagnostics" / "collector-diag.txt"
    text = report.read_text(encoding="utf-8")
    assert "=== Providers ===" in text
    assert "[fake:logitech]" in text
    assert "=== Key aliases ===" in text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process tree check")
def test_once_with_all_providers_disabled_exits_quickly(tmp_path):
    """Audit P3-7: no enabled providers must not wait 120 s for a snapshot."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(
        json.dumps({"providers": [], "bluetooth": False}), encoding="utf-8")
    started = time.monotonic()
    result = _run(["--once"], tmp_path)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < 15.0, f"--once took {elapsed:.1f}s"
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert messages[0]["t"] == "hello"
    assert messages[0]["devices"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process tree check")
def test_shutdown_with_a_blocked_provider_exits_under_two_seconds(tmp_path):
    """P10/P1: a provider blocked in poll() must not delay process exit.

    ``--scenario slow`` makes every fake provider sleep 5 s inside poll(); the
    old ThreadPoolExecutor (non-daemon threads) kept the process alive for the
    full block after shutdown (measured 6.1 s).
    """
    data_dir = tmp_path / "data"
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "--stdio", "--fake",
         "--scenario", "slow", "--speed", "20", "--data-dir", str(data_dir)],
        cwd=ROOT, env=_env(tmp_path), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", bufsize=1)
    elapsed = None
    code = None
    try:
        hello = json.loads(proc.stdout.readline())
        assert hello["t"] == "hello"
        time.sleep(0.5)                    # providers are now blocked in poll()
        started = time.monotonic()
        proc.stdin.write('{"t":"shutdown"}\n')
        proc.stdin.flush()
        code = proc.wait(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert code == 0
    assert elapsed is not None and elapsed < 2.0, f"shutdown took {elapsed:.2f}s"

    # no orphan collector process may keep the data dir open (P10)
    marker = str(data_dir)
    query = ("@(Get-CimInstance Win32_Process | Where-Object { "
             f"$_.CommandLine -like '*{marker}*' -and $_.Name -notmatch 'powershell' "
             "}).Count")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() in ("0", ""), result.stdout
