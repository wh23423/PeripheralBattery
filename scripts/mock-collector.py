"""测试夹具：最小合法 protocol v1 collector（NDJSON over stdio）。

用途：验证 Tauri 外壳的 collector stdio → Rust 托盘镜像 / 前端 keyed 渲染 /
set_tray_state 托盘变色整条链路。它不读写真实硬件，只按剧本发协议事件。

用法（PB_COLLECTOR，整条命令，路径含空格时加引号）：
  PB_COLLECTOR="<项目根>\\.venv\\Scripts\\python.exe <项目根>\\scripts\\mock-collector.py"

可选测试钩子（环境变量）：
  PB_MOCK_PROTOCOL=2       hello 用 protocol=2，验证「采集器版本过期」错误路径
  PB_MOCK_MUTATE_MS=<ms>   首轮数据后每 <ms> 毫秒改一次设备电量/充电并推
                           device+tick，用于验证「隐藏期间托盘仍更新 / resume 补快照」

协议依据：protocol.md §1/§2（hello / device / tick / alert，命令 shutdown/refresh/get）。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

# 测试钩子：PB_MOCK_PROTOCOL=2 可验证 app 的“采集器版本过期”错误路径。
PROTOCOL = int(os.environ.get("PB_MOCK_PROTOCOL", "1"))
MUTATE_MS = int(os.environ.get("PB_MOCK_MUTATE_MS", "0") or "0")
SESSION = str(uuid.uuid4())
BUILD = "mock-shell-fixture"

# 与真实 collector 一致：强制 UTF-8 行缓冲（stdout 可能因 --noconsole/管道退化为 GBK）。
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", newline="\n", line_buffering=True)
except Exception:
    pass

LOCK = threading.Lock()
SEQ = [1]


def device(key, name, level, kind, transport, *, charging=False, online=True,
           approx="", left_s=None):
    return {
        "key": key,
        "name": name,
        "level": level,
        "approx": approx,
        "left_s": left_s,
        "charging": charging,
        "online": online,
        "kind": kind,
        "transport": transport,
        "via": "",
        "updated_unix": int(time.time()),
    }


DEVICES = [
    device("logi:046d:c547:unit-12", "G502 X PLUS", 68, "mouse", "2.4g", left_s=6480),
    device("ss:1038:2202:serial-abc", "Arctis Nova 7", 12, "headset", "2.4g",
           left_s=900),
    device("keychron:3434:0200:serial-k2", "Keychron K2", None, "keyboard",
           "bluetooth", approx="约 55%（中）"),
    device("xinput:045e:0b13:u3", "Xbox Wireless Controller", 84, "gamepad",
           "2.4g", charging=True),
    device("bt:0000:0000:aabbccddeeff", "WH-1000XM5", 43, "bluetooth", "bluetooth"),
]


def write_raw(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def emit(obj) -> None:
    """自动分配 seq（hello 除外，hello 固定 seq=0）并串行化写 stdout。"""
    with LOCK:
        if obj.get("t") != "hello":
            obj["seq"] = SEQ[0]
            SEQ[0] += 1
        write_raw(obj)


def snapshot_devices():
    return [dict(d) for d in DEVICES]


def mutate_loop(interval_ms: int) -> None:
    """隐藏态托盘验证剧本：headset 12%↔55% 并切换 charging；gamepad 电量变化。"""
    i = 0
    while True:
        time.sleep(interval_ms / 1000.0)
        i += 1
        headset = DEVICES[1]
        headset["level"] = 12 if i % 2 else 55
        headset["charging"] = (i % 2 == 1)
        headset["updated_unix"] = int(time.time())
        gamepad = DEVICES[3]
        gamepad["level"] = 84 - (i % 5) * 10
        gamepad["updated_unix"] = int(time.time())
        emit({"t": "device", "session": SESSION, "device": dict(headset)})
        emit({"t": "device", "session": SESSION, "device": dict(gamepad)})
        if i % 2 == 0:
            emit({"t": "tick", "session": SESSION, "devices": snapshot_devices()})


def main() -> int:
    emit({
        "t": "hello",
        "protocol": PROTOCOL,
        "seq": 0,
        "session": SESSION,
        "build": BUILD,
        "capabilities": ["approx", "left_s", "via", "transport", "replaces"],
        "devices": [],
        "settings": {
            "poll": {"mouse": 5, "keyboard": 15, "headset": 15, "gamepad": 10, "steam": 30},
            "providers": ["mock"],
        },
    })
    # 模拟首轮采集耗时：分批推 device（给前端 keyed diff / 入场动画一个真实节奏）
    for dev in DEVICES:
        time.sleep(0.18)
        emit({"t": "device", "session": SESSION, "device": dev})
    time.sleep(0.1)
    emit({
        "t": "alert", "session": SESSION,
        "kind": "low", "key": "ss:1038:2202:serial-abc", "level": 12,
        "text": "Arctis Nova 7 电量低",
    })
    # P8 可观测性：一条 stat（验证 app.log 摘要保留 timers_per_s/key_migrations）
    emit({
        "t": "stat", "session": SESSION,
        "providers": {
            "mock": {"polls": 1, "avg_ms": 12.3, "last_ms": 10.0},
            "xinput": {"polls": 2, "avg_ms": 31.0, "last_ms": 28.0},
        },
        "timers_per_s": 0.99,
        "key_migrations": 0,
    })

    if MUTATE_MS > 0:
        threading.Thread(target=mutate_loop, args=(MUTATE_MS,), daemon=True).start()

    last_tick = time.monotonic()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = cmd.get("t")
        if t == "shutdown":
            return 0
        if t == "refresh":
            emit({"t": "tick", "session": SESSION, "devices": snapshot_devices()})
            last_tick = time.monotonic()
        elif t == "get":
            emit({"t": "tick", "session": SESSION, "devices": snapshot_devices()})
        if time.monotonic() - last_tick > 10:
            emit({"t": "tick", "session": SESSION, "devices": snapshot_devices()})
            last_tick = time.monotonic()
    return 0


if __name__ == "__main__":
    sys.exit(main())
