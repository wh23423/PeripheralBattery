"""Hardened stdio / log plumbing (BUILD_PLAN §1.1, protocol.md §0).

Five things that break a `--noconsole` PyInstaller build, all handled here:

1. ``sys.stdout`` / ``sys.stderr`` can be ``None``: reopen file descriptor 1/2.
2. Both ends are UTF-8 (device names contain Chinese), ``\\n`` line endings, no BOM.
3. Every line is flushed: block buffering would hold messages up to 4 KiB.
4. stderr is drained by its own thread into a rotating log file. The upstream
   Bluetooth provider logs on every device every round; a full stderr pipe would
   otherwise block the collector inside ``logging``.
5. Both directions cap the line length: an over-long line is dropped and the
   reader resynchronises at the next newline; a partial line at EOF is discarded.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from typing import Any, Iterable, Iterator, Optional, TextIO

MAX_LINE = 1024 * 1024          # protocol.md §0: one line <= 1 MiB
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 5

log = logging.getLogger("halo_battery")


# ---------------------------------------------------------------- stdio setup
def ensure_utf8_stdio() -> None:
    """Make stdout/stderr usable: reopen when missing, UTF-8 + flush per line."""
    for name, fd in (("stdout", 1), ("stderr", 2)):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                stream = os.fdopen(fd, "w", encoding="utf-8", errors="replace",
                                   newline="\n", buffering=1)
            except OSError:                      # no console and fd not inherited
                stream = open(os.devnull, "w", encoding="utf-8", newline="\n")
            setattr(sys, name, stream)
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n",
                               line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass


# ---------------------------------------------------------------- stderr drain
class _BytesAdapter:
    """``sys.stderr.buffer`` for code that writes bytes (traceback machinery, libs)."""

    def __init__(self, stream: "QueueStream"):
        self._stream = stream

    def write(self, data: Any) -> int:
        if isinstance(data, (bytes, bytearray)):
            self._stream.write(bytes(data).decode("utf-8", "replace"))
        else:
            self._stream.write(str(data))
        return len(data)

    def flush(self) -> None:
        pass


class QueueStream:
    """A text stream that only enqueues; the rotating-log thread does the I/O."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, sink: "RotatingLog"):
        self._sink = sink
        self.buffer = _BytesAdapter(self)

    def write(self, text: str) -> int:
        if text:
            self._sink.write(text)
        return len(text)

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("stderr is redirected to a rotating log file")

    @property
    def closed(self) -> bool:
        return self._sink.closed


class RotatingLog:
    """One writer thread, one open file, simple size-based rotation.

    Never raises into the caller: a full disk or a permission error disables the
    file but the queue keeps draining, so the collector cannot deadlock on logging.
    """

    def __init__(self, path: str, max_bytes: int = LOG_MAX_BYTES,
                 backups: int = LOG_BACKUPS):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.closed = False
        self.dropped = 0
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="collector-stderr",
                                        daemon=True)

    def start(self) -> "RotatingLog":
        self._thread.start()
        return self

    def write(self, text: str) -> None:
        if not self.closed:
            self._q.put(text)

    def close(self, timeout: float = 2.0) -> None:
        if self.closed:
            return
        self.closed = True
        self._q.put(None)
        self._thread.join(timeout)

    def _rotate(self, f: TextIO) -> TextIO:
        try:
            f.flush()
            f.close()
        except OSError:
            pass
        try:
            for i in range(self.backups - 1, 0, -1):
                src = f"{self.path}.{i}"
                if os.path.exists(src):
                    os.replace(src, f"{self.path}.{i + 1}")
            if os.path.exists(self.path):
                os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass
        return self._open()

    def _open(self) -> Optional[TextIO]:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            return open(self.path, "a", encoding="utf-8", errors="replace",
                        newline="\n", buffering=1)
        except OSError:
            return None

    def _run(self) -> None:
        f = self._open()
        pending: list[str] = []
        while True:
            item = self._q.get()
            if item is None:
                break
            pending.append(item)
            while True:                           # batch whatever is already queued
                try:
                    more = self._q.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    item = None
                    break
                pending.append(more)
            text = "".join(pending)
            pending.clear()
            if f is not None:
                try:
                    f.write(text)
                    f.flush()
                    if f.tell() > self.max_bytes:
                        f = self._rotate(f)
                except (OSError, ValueError):
                    self.dropped += 1
                    try:
                        f.close()
                    except OSError:
                        pass
                    f = None
            else:
                self.dropped += 1
            if item is None:                      # sentinel seen inside the batch
                break
        if f is not None:
            try:
                f.flush()
                f.close()
            except OSError:
                pass


_installed: Optional[RotatingLog] = None


def install_stderr_log(path: str) -> RotatingLog:
    """Point sys.stderr and the logging module at a rotating log file.

    Must be called before anything logs. Returns the writer so the caller can
    close it (and so tests can flush it).
    """
    global _installed
    ensure_utf8_stdio()
    writer = RotatingLog(path).start()
    sys.stderr = QueueStream(writer)       # type: ignore[assignment]
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    # The upstream providers log through this logger; keep it at INFO so the
    # log is useful for device diagnostics.
    logging.getLogger("halo_battery").setLevel(logging.INFO)
    _installed = writer
    return writer


def close_stderr_log(writer: Optional[RotatingLog] = None) -> None:
    global _installed
    target = writer or _installed
    if target is not None:
        target.close()
    _installed = None


# ---------------------------------------------------------------- line writer
class LineWriter:
    """One JSON object per line, UTF-8, flushed immediately, length-capped."""

    def __init__(self, stream: Optional[TextIO] = None, max_line: int = MAX_LINE):
        self._stream = stream if stream is not None else sys.stdout
        self.max_line = max_line
        self._lock = threading.Lock()
        self.dropped = 0
        self.written = 0

    def send(self, obj: Any) -> bool:
        try:
            line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            log.exception("could not encode an outgoing message")
            self.dropped += 1
            return False
        data = line + "\n"
        if len(data.encode("utf-8")) > self.max_line:
            log.error("outgoing line over %d bytes dropped", self.max_line)
            self.dropped += 1
            return False
        try:
            with self._lock:
                if self._stream is None:
                    self.dropped += 1
                    return False
                self._stream.write(data)
                self._stream.flush()
                self.written += 1
            return True
        except (OSError, ValueError):
            self.dropped += 1
            return False


# ---------------------------------------------------------------- line reader
def _decode(chunk: Any) -> str:
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk).decode("utf-8", "replace")
    return str(chunk)


def read_commands(stream: Optional[Any] = None,
                  max_line: int = MAX_LINE) -> Iterator[dict]:
    """Yield one dict per stdin line; never raise on bad input.

    * over-long lines are dropped whole and the reader resynchronises;
    * a partial line at EOF is discarded (protocol.md §0);
    * invalid UTF-8 becomes U+FFFD, invalid JSON is logged and skipped.
    """
    stream = stream if stream is not None else sys.stdin
    if stream is None:
        return
    # Prefer the binary buffer: readline(limit) counts bytes there, which is what
    # the line cap means. Text streams are accepted for tests.
    src = getattr(stream, "buffer", stream)
    while True:
        try:
            chunk = src.readline(max_line + 1)
        except (OSError, ValueError):
            return
        if not chunk:
            return                                    # EOF; any partial line dropped
        # Length decisions are made on BYTES. Decoding first was the audit P3-1
        # bug: a >1 MiB CJK line decodes to fewer characters than max_line and was
        # mistaken for an EOF fragment, which killed the whole command stream.
        if isinstance(chunk, (bytes, bytearray)):
            raw = bytes(chunk)
        else:
            raw = str(chunk).encode("utf-8", "replace")
        if not raw.endswith(b"\n"):
            if len(raw) > max_line:                   # over-long line: drain it
                drained = len(raw)
                guard = 0
                while drained <= 64 * max_line and guard < 4096:
                    try:
                        more = src.readline(max_line + 1)
                    except (OSError, ValueError):
                        return
                    guard += 1
                    if not more:
                        break
                    more_raw = (bytes(more) if isinstance(more, (bytes, bytearray))
                                else str(more).encode("utf-8", "replace"))
                    drained += len(more_raw)
                    if more_raw.endswith(b"\n"):
                        break
                log.warning("stdin line over %d bytes dropped", max_line)
                continue
            return                                    # EOF with a partial line
        if len(raw) - 1 > max_line:                   # too long even with newline
            log.warning("stdin line over %d bytes dropped", max_line)
            continue
        line = raw.decode("utf-8", "replace").strip("\r\n")
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            log.warning("stdin: not JSON: %r", line[:200])
            continue
        if not isinstance(msg, dict):
            log.warning("stdin: not a JSON object: %r", line[:200])
            continue
        yield msg


class StdioTransport:
    """The two directions together: JSONL out, JSONL commands in."""

    def __init__(self, stream: Optional[TextIO] = None,
                 max_line: int = MAX_LINE):
        self.writer = LineWriter(stream, max_line)
        self.max_line = max_line

    def send(self, obj: Any) -> bool:
        return self.writer.send(obj)

    def commands(self) -> Iterator[dict]:
        return read_commands(None, self.max_line)

    def flush(self) -> None:
        pass
