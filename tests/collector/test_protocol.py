"""Protocol envelopes (protocol.md §1): fields, seq, session, device validation."""
from __future__ import annotations

import json

import pytest

from collector.protocol import (CAPABILITIES, PROTOCOL_VERSION, Protocol,
                                RecordingSink, make_device, semantic_equal)


class SpyWriter:
    def __init__(self):
        self.messages = []
        self.flushes = 0

    def send(self, obj):
        self.messages.append(obj)
        return True


def _proto():
    writer = SpyWriter()
    return Protocol(writer, session="session-1", build="abc123"), writer


def test_hello_envelope():
    protocol, writer = _proto()
    protocol.hello([], {"poll": {"mouse": 5}, "providers": ["logitech"]})
    hello = writer.messages[0]
    assert hello["t"] == "hello"
    assert hello["protocol"] == PROTOCOL_VERSION
    assert hello["session"] == "session-1"
    assert hello["build"] == "abc123"
    assert hello["seq"] == 0
    assert hello["capabilities"] == CAPABILITIES
    assert hello["devices"] == []
    assert hello["settings"]["poll"]["mouse"] == 5


def test_seq_is_monotonic_and_counts_every_event():
    protocol, writer = _proto()
    protocol.hello([])
    device = make_device("a:0000:0001:u1", "测试设备", 50)
    protocol.device(device)
    protocol.gone(device["key"])
    protocol.tick([device])
    protocol.alert("low", device["key"], 10, "电量低")
    protocol.stat({"logitech": {"polls": 1}}, 0.2, 3)
    seqs = [m["seq"] for m in writer.messages]
    assert seqs == [0, 1, 2, 3, 4, 5]
    assert all(m["session"] == "session-1" for m in writer.messages)


def test_session_restart_resets_seq_but_not_session_of_old_events():
    protocol, writer = _proto()
    protocol.hello([])
    protocol.device(make_device("a:0000:0001:u1", "x", 50))
    old_session = protocol.session
    protocol.new_session()
    assert protocol.session != old_session
    assert protocol.seq == 0
    protocol.hello([])
    assert writer.messages[-1]["session"] == protocol.session
    assert writer.messages[-1]["seq"] == 0


def test_device_validation_level_kind_transport():
    unknown = make_device("k", "name", level=None)
    assert unknown["level"] is None
    assert make_device("k", "name", level=150)["level"] == 100
    assert make_device("k", "name", level=-3)["level"] == 0
    assert make_device("k", "name", level=True)["level"] is None
    invalid = make_device("k", "name", 5, kind="weird", transport="carrier-pigeon")
    assert invalid["kind"] == "mouse"
    assert invalid["transport"] == "unknown"
    assert make_device("k", "name", 5, kind="weird", transport="bluetooth")["kind"] == "bluetooth"
    device = make_device("k", "n", 44, approx="约 55%（中）", left_s=6480,
                         charging=True, online=False, kind="headset",
                         transport="2.4g", via="bluetooth", updated_unix=123)
    assert device["left_s"] == 6480
    assert device["updated_unix"] == 123
    assert device["name"] == "n"


def test_device_without_name_gets_placeholder():
    assert make_device("k", "   ")["name"] == "未知设备"


def test_semantic_equality_ignores_updated_unix():
    a = make_device("k", "n", 50, updated_unix=1)
    b = make_device("k", "n", 50, updated_unix=2)
    assert semantic_equal(a, b)
    b["level"] = 49
    assert not semantic_equal(a, b)
    assert not semantic_equal(None, a)


def test_alert_kind_is_validated():
    protocol, writer = _proto()
    assert protocol.alert("low", "k", 10, "text")["kind"] == "low"
    with pytest.raises(ValueError):
        protocol.alert("explode", "k")


def test_replaces_is_only_on_device_and_never_equal_to_the_new_key():
    protocol, writer = _proto()
    device = make_device("logitech:046d:c547:unit-1", "G502", 60)
    protocol.device(device, "logitech:046d:c547:u1")
    assert writer.messages[-1]["replaces"] == "logitech:046d:c547:u1"
    protocol.device(device, device["key"])
    assert "replaces" not in writer.messages[-1]


def test_messages_are_json_serialisable_utf8():
    protocol, writer = _proto()
    protocol.device(make_device("k", "客厅手柄", 30))
    line = json.dumps(writer.messages[-1], ensure_ascii=False, separators=(",", ":"))
    assert "客厅手柄" in line
    assert json.loads(line)["device"]["name"] == "客厅手柄"


def test_recording_sink_helpers():
    sink = RecordingSink()
    device = make_device("k", "n", 5)
    sink.device(device)
    sink.gone("k")
    assert len(sink.by_type("device")) == 1
    assert sink.last("gone")["key"] == "k"
    sink.clear()
    assert sink.messages == []
