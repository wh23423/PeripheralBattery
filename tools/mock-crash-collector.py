#!/usr/bin/env python3
"""A13 验收夹具：会“崩溃”的 collector（最小合法 protocol v1，NDJSON over stdio）。

用途：验证 sidecar 在 collector 非正常退出后的自动重启 / 托盘离线 / 重试用尽。
它不碰真实硬件，只按剧本发 hello/tick，然后按配置退出。

环境变量：
  PB_CRASH_COUNTER   计数文件路径（必填，用于跨进程累计启动次数）
  PB_CRASH_TIMES     前 N 次启动以 exit code 2 崩溃（默认 2）
  PB_CRASH_AFTER_MS  崩溃前等待毫秒（默认 1500；给 sidecar 足够时间完成 hello）
  PB_CRASH_MODE      crash=前 N 次崩溃；live=永不崩溃（用于验证恢复）
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

ATTEMPT = 0
COUNTER = os.environ.get("PB_CRASH_COUNTER", "")
CRASH_TIMES = int(os.environ.get("PB_CRASH_TIMES", "2") or "2")
CRASH_AFTER_MS = int(os.environ.get("PB_CRASH_AFTER_MS", "1500") or "1500")
MODE = os.environ.get("PB_CRASH_MODE", "crash")

try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", newline="\n", line_buffering=True)
except Exception:
    pass

SESSION = str(uuid.uuid4())
SEQ = [0]


def emit(message: dict, counted: bool = True) -> None:
    if counted:
        SEQ[0] += 1
        message["seq"] = SEQ[0]
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def bump_attempt() -> int:
    if not COUNTER:
        return 1
    try:
        with open(COUNTER, "r", encoding="utf-8") as f:
            n = int((f.read() or "0").strip() or "0")
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        d = os.path.dirname(COUNTER)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(COUNTER, "w", encoding="utf-8") as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def device(level: int = 77, online: bool = True) -> dict:
    return {
        "key": "mock:crash:1",
        "name": "崩溃夹具设备",
        "level": level,
        "approx": "",
        "left_s": None,
        "charging": False,
        "online": online,
        "kind": "mouse",
        "transport": "usb",
        "via": "",
        "updated_unix": int(time.time()),
    }


def send_hello() -> None:
    emit(
        {
            "t": "hello",
            "protocol": 1,
            "seq": 0,
            "session": SESSION,
            "build": "mock-crash-fixture",
            "capabilities": ["refresh", "shutdown", "get"],
            "devices": [],
        },
        counted=False,
    )


def send_tick(level: int = 77, online: bool = True) -> None:
    emit({"t": "tick", "devices": [device(level, online)]})


def main() -> int:
    attempt = bump_attempt()
    send_hello()
    send_tick()
    sys.stderr.write(f"mock-crash: attempt={attempt} mode={MODE}\n")
    sys.stderr.flush()

    if MODE != "live" and attempt <= CRASH_TIMES:
        time.sleep(CRASH_AFTER_MS / 1000.0)
        sys.stderr.write(f"mock-crash: simulating crash attempt={attempt}\n")
        sys.stderr.flush()
        return 2

    # 恢复模式：保持在线，直到收到 shutdown 或 stdin 关闭。
    started = time.time()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("t") == "shutdown":
            return 0
        if msg.get("t") == "refresh":
            send_tick()
        if time.time() - started > 120:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
