#!/usr/bin/env python
"""Validate collector NDJSON against protocol.md §1.

Usage:
    python tools/collector_ndjson_check.py <file.ndjson>
    python -m collector --once ... | python tools/collector_ndjson_check.py -

Checks (exit code 1 on the first hard failure):
  * UTF-8, no BOM, exactly one JSON object per line;
  * first message is hello with protocol 1, session/build/capabilities/devices/
    settings and seq 0;
  * every later message has the same session and a strictly increasing seq;
  * only the event types of the contract are used;
  * Device objects have every field, level is int 0..100 or null, kind and
    transport are the documented enums, key matches provider:vid:pid:suffix;
  * gone carries a key, tick carries a device list, alert has a known kind.
"""
from __future__ import annotations

import json
import re
import sys

KINDS = {"headset", "mouse", "keyboard", "gamepad", "bluetooth"}
TRANSPORTS = {"2.4g", "usb", "bluetooth", "unknown"}
ALERTS = {"low", "full", "offline"}
EVENTS = {"device", "gone", "tick", "alert", "stat"}
LEFT_S_SRC = {"", "history", "spec", "mains"}
DEVICE_FIELDS = {"key", "name", "level", "approx", "left_s", "left_s_src", "charging",
                 "online", "kind", "transport", "via", "updated_unix"}
KEY_RE = re.compile(
    r"^[a-z0-9_]+:[0-9a-f]{4}:[0-9a-f]{4}:(?:[a-z0-9]+|unit-[a-z0-9]+|u[0-9]+)$")


class Problems:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, line_no, message):
        self.errors.append(f"line {line_no}: {message}")

    def warn(self, line_no, message):
        self.warnings.append(f"line {line_no}: {message}")


def check_device(problems, line_no, device, label="device"):
    if not isinstance(device, dict):
        problems.error(line_no, f"{label} is not an object")
        return
    missing = DEVICE_FIELDS - set(device)
    extra = set(device) - DEVICE_FIELDS
    if missing:
        problems.error(line_no, f"{label} missing fields: {sorted(missing)}")
    if extra:
        problems.warn(line_no, f"{label} unknown fields: {sorted(extra)}")
    level = device.get("level")
    if level is not None and (not isinstance(level, int) or isinstance(level, bool)
                              or not 0 <= level <= 100):
        problems.error(line_no, f"{label} bad level {level!r}")
    if device.get("kind") not in KINDS:
        problems.error(line_no, f"{label} bad kind {device.get('kind')!r}")
    if device.get("transport") not in TRANSPORTS:
        problems.error(line_no, f"{label} bad transport {device.get('transport')!r}")
    for field in ("charging", "online"):
        if not isinstance(device.get(field), bool):
            problems.error(line_no, f"{label}.{field} is not a bool")
    if not isinstance(device.get("name"), str) or not device.get("name"):
        problems.error(line_no, f"{label}.name is empty")
    key = device.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        problems.error(line_no, f"{label} key not provider:vid:pid:suffix: {key!r}")
    if not isinstance(device.get("updated_unix"), int):
        problems.error(line_no, f"{label}.updated_unix is not an int")
    if device.get("left_s") is not None and not isinstance(device["left_s"], int):
        problems.error(line_no, f"{label}.left_s is not int/null")
    if device.get("left_s_src") not in LEFT_S_SRC:
        problems.error(line_no, f"{label}.left_s_src is not one of {sorted(LEFT_S_SRC)!r}")


def main(argv):
    path = argv[1] if len(argv) > 1 else "-"
    if path == "-":
        raw = sys.stdin.buffer.read()
    else:
        with open(path, "rb") as f:
            raw = f.read()
    problems = Problems()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.error(1, "BOM at the start of the stream")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"FAIL: stream is not UTF-8: {exc}")
        return 1
    lines = text.splitlines()
    messages = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            problems.error(number, f"not JSON: {exc}")
            continue
        if not isinstance(message, dict):
            problems.error(number, "line is not a JSON object")
            continue
        messages.append((number, message))

    if not messages:
        print("FAIL: no messages")
        return 1
    number, hello = messages[0]
    if hello.get("t") != "hello":
        problems.error(number, f"first message is {hello.get('t')!r}, not hello")
    if hello.get("protocol") != 1:
        problems.error(number, f"protocol is {hello.get('protocol')!r}, expected 1")
    if hello.get("seq") != 0:
        problems.error(number, f"hello seq is {hello.get('seq')!r}, expected 0")
    session = hello.get("session")
    if not isinstance(session, str) or not session:
        problems.error(number, "hello has no session")
    if not isinstance(hello.get("build"), str):
        problems.error(number, "hello has no build string")
    capabilities = hello.get("capabilities")
    if not isinstance(capabilities, list):
        problems.error(number, "hello has no capabilities list")
    if not isinstance(hello.get("devices"), list):
        problems.error(number, "hello has no devices list")
    else:
        for device in hello["devices"]:
            check_device(problems, number, device, "hello device")
    if not isinstance(hello.get("settings"), dict):
        problems.error(number, "hello has no settings object")

    previous_seq = 0
    counts = {}
    devices = {}
    for number, message in messages[1:]:
        event = message.get("t")
        if event not in EVENTS:
            problems.error(number, f"unknown event type {event!r}")
            continue
        counts[event] = counts.get(event, 0) + 1
        if message.get("session") != session:
            problems.error(number, f"session {message.get('session')!r} != hello session")
        seq = message.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            problems.error(number, "seq is not an int")
        else:
            if seq <= previous_seq:
                problems.error(number, f"seq {seq} is not > {previous_seq}")
            previous_seq = seq
        if event == "device":
            check_device(problems, number, message.get("device"), "device")
            replaces = message.get("replaces")
            if replaces is not None and not isinstance(replaces, str):
                problems.error(number, "replaces is not a string")
        elif event == "gone":
            if not isinstance(message.get("key"), str):
                problems.error(number, "gone has no key")
        elif event == "tick":
            if not isinstance(message.get("devices"), list):
                problems.error(number, "tick has no devices list")
            else:
                for device in message["devices"]:
                    check_device(problems, number, device, "tick device")
        elif event == "alert":
            if message.get("kind") not in ALERTS:
                problems.error(number, f"alert kind {message.get('kind')!r}")
            if not isinstance(message.get("key"), str):
                problems.error(number, "alert has no key")
        elif event == "stat":
            if not isinstance(message.get("providers"), dict):
                problems.error(number, "stat has no providers map")
            if not isinstance(message.get("timers_per_s"), (int, float)):
                problems.error(number, "stat has no timers_per_s")

    for _, message in messages:
        if message.get("t") == "hello":
            for device in message.get("devices") or []:
                devices[device.get("key")] = device
        elif message.get("t") == "device":
            device = message.get("device") or {}
            devices[device.get("key")] = device
        elif message.get("t") == "tick":
            for device in message.get("devices") or []:
                devices[device.get("key")] = device

    print(f"messages: hello + {len(messages) - 1} event(s)")
    print(f"types: {counts}")
    print(f"devices seen: {len(devices)}")
    for key, device in devices.items():
        level = "unknown" if device.get("level") is None else f"{device['level']}%"
        print(f"  {key}  name={device.get('name')!r} level={level} "
              f"kind={device.get('kind')} transport={device.get('transport')}")
    for warning in problems.warnings:
        print(f"WARN {warning}")
    if problems.errors:
        for error in problems.errors:
            print(f"FAIL {error}")
        return 1
    print("OK: NDJSON matches protocol.md §1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
