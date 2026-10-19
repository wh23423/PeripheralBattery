"""Track every child process the collector spawns (audit P2-1 residual).

The provider package (copied unmodified from upstream) uses ``subprocess.run`` for
the WGI query and the one-shot Bluetooth fallback; the Bluetooth watcher uses
``subprocess.Popen`` directly. A daemon *thread* cannot be killed, so on shutdown
the child processes must be terminated explicitly or they become orphans (P10).

This module wraps ``subprocess.Popen`` once per process with a registry-backed
subclass. ``terminate_all()`` is called from ``Collector.stop()``: terminate,
wait 500 ms, kill whatever is left. Only real-mode collectors install it; fake
runs (tests/demo) never spawn children.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("halo_battery")

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _JobGuard:
    """A Windows Job Object that kills every assigned child when we die.

    ``TerminateProcess`` on the collector (Rust's 2 s fallback) gives Python no
    chance to run cleanup, so the only reliable way to reap grandchildren spawned
    by ``subprocess.run`` is to put them in a kill-on-close job. Assignment is
    best effort: when the collector already lives in the app's Job Object and
    nested assignment is refused, we log and fall back to the registry's
    terminate-on-shutdown.
    """

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        self._assign_failed = 0
        if sys.platform != "win32":
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = ctypes.c_void_p
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error())
            info = self._limit_info()
            ok = kernel32.SetInformationJobObject(
                ctypes.c_void_p(handle), _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                raise OSError(ctypes.get_last_error())
            self.handle = handle
            self._kernel32 = kernel32
        except Exception as exc:            # pragma: no cover - platform specific
            log.warning("could not create child Job Object: %s", exc)
            self.handle = None

    @staticmethod
    def _limit_info():
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        return info

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is None or sys.platform != "win32":
            return
        try:
            kernel32 = self._kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                             ctypes.c_uint32]
            kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p,
                                                          ctypes.c_void_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                                           False, proc.pid)
            if not process:
                raise OSError(ctypes.get_last_error())
            try:
                if not kernel32.AssignProcessToJobObject(
                        ctypes.c_void_p(self.handle), ctypes.c_void_p(process)):
                    raise OSError(ctypes.get_last_error())
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(process))
        except Exception as exc:            # pragma: no cover - platform specific
            self._assign_failed += 1
            if self._assign_failed <= 2:
                log.warning("could not assign child pid %s to the Job Object: %s",
                            getattr(proc, "pid", "?"), exc)

    def close(self) -> None:
        if self.handle is not None and sys.platform == "win32":
            try:
                self._kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            except Exception:
                pass
            self.handle = None


class ChildRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: Dict[int, subprocess.Popen] = {}
        self._orig_popen: Optional[type] = None
        self._installed = False
        self._job: Optional[_JobGuard] = None

    # ------------------------------------------------------------ install
    def install(self) -> "ChildRegistry":
        if self._installed:
            return self
        registry = self

        class TrackedPopen(subprocess.Popen):        # type: ignore[misc,valid-type]
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                registry._add(self)

            def poll(self):
                code = super().poll()
                if code is not None:
                    registry._discard(self)
                return code

            def wait(self, timeout=None):
                try:
                    return super().wait(timeout)
                finally:
                    if super().poll() is not None:
                        registry._discard(self)

        self._orig_popen = subprocess.Popen
        subprocess.Popen = TrackedPopen             # type: ignore[assignment]
        self._installed = True
        self._job = _JobGuard()
        return self

    def uninstall(self) -> None:
        if self._installed and self._orig_popen is not None:
            subprocess.Popen = self._orig_popen     # type: ignore[assignment]
        self._installed = False
        if self._job is not None:
            self._job.close()
            self._job = None

    @property
    def job_active(self) -> bool:
        return self._job is not None and self._job.handle is not None

    # ------------------------------------------------------------ tracking
    def _add(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children[id(proc)] = proc
        if self._job is not None:
            self._job.assign(proc)

    def _discard(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children.pop(id(proc), None)

    def alive(self) -> List[subprocess.Popen]:
        with self._lock:
            return [p for p in self._children.values() if p.poll() is None]

    def count(self) -> int:
        return len(self.alive())

    # ------------------------------------------------------------ shutdown
    def terminate_all(self, timeout: float = 0.5) -> int:
        """Terminate every live child: terminate -> 500 ms -> kill."""
        alive = self.alive()
        for proc in alive:
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + max(0.0, timeout)
        for proc in alive:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except Exception:
                pass
        for proc in alive:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    log.warning("child process %s did not exit after kill", proc.pid)
        return len(alive)


_registry = ChildRegistry()


def install() -> ChildRegistry:
    return _registry.install()


def terminate_all(timeout: float = 0.5) -> int:
    return _registry.terminate_all(timeout)


def registry() -> ChildRegistry:
    return _registry
