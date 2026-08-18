import json
from io import StringIO

import pytest

from herethere.everywhere.live import (
    MAX_TRACEBACK_BYTES,
    bounded_utf8_tail,
    execute_live,
)
from herethere.everywhere.protocol import (
    WORKER_OUTPUT_TRUNCATION_MARKER,
    CapturedStreamEvent,
    EventStream,
    OrderedBoundedOutputCollector,
    write_event,
)


def test_bounded_utf8_tail_drops_partial_character():
    assert bounded_utf8_tail("x€", 2) == ""


def test_bounded_utf8_tail_never_exceeds_byte_limit():
    limit = 64 * 1024
    result = bounded_utf8_tail("€" + "a" * (limit - 1), limit)

    assert len(result.encode("utf-8")) <= limit
    assert result == "a" * (limit - 1)


def test_execute_live_success_and_failure_share_namespace():
    namespace = {}
    stdout = StringIO()
    stderr = StringIO()

    assert execute_live("answer = 42\nprint(answer)", namespace, stdout, stderr) is None
    error = execute_live("raise ValueError('bad')", namespace, stdout, stderr)

    assert namespace["answer"] == 42
    assert stdout.getvalue() == "42\n"
    assert error.remote_type == "ValueError"
    assert error.message == "bad"
    assert error.asdict()["traceback"] == error.traceback
    assert "ValueError: bad" in stderr.getvalue()
    assert len(error.traceback.encode("utf-8")) <= MAX_TRACEBACK_BYTES


def test_protocol_event_writer_and_stream():
    output = StringIO()
    stream = EventStream(output, "stdout")

    assert stream.write("") == 0
    assert stream.write("hello") == 5
    stream.flush()
    write_event(output, {"type": "result", "ok": True})

    assert [json.loads(line) for line in output.getvalue().splitlines()] == [
        {"type": "stream", "stream": "stdout", "data": "hello"},
        {"type": "result", "ok": True},
    ]


def test_ordered_output_collector_preserves_and_coalesces_writes():
    collector = OrderedBoundedOutputCollector(byte_limit=100, event_limit=4)

    assert collector.stdout.encoding == "utf-8"
    assert collector.stdout.writable()
    assert collector.stdout.write("") == 0
    assert collector.stdout.write("A") == 1
    assert collector.stdout.write("B") == 1
    collector.stdout.flush()
    assert collector.stderr.write("C") == 1

    assert collector.events == [
        CapturedStreamEvent("stdout", "AB"),
        CapturedStreamEvent("stderr", "C"),
    ]
    assert collector.events[0].asdict() == {
        "type": "stream",
        "stream": "stdout",
        "data": "AB",
    }
    assert collector.payload_bytes == 3
    assert collector.truncated is False


def test_ordered_output_collector_truncates_at_valid_utf8_prefix():
    collector = OrderedBoundedOutputCollector(byte_limit=4, event_limit=4)

    assert collector.stdout.write("a€x") == 3
    assert collector.stderr.write("ignored") == 7

    assert collector.payload_bytes == 4
    assert collector.truncated is True
    assert collector.events == [
        CapturedStreamEvent("stdout", "a€"),
        CapturedStreamEvent("stderr", WORKER_OUTPUT_TRUNCATION_MARKER),
    ]


def test_ordered_output_collector_marks_only_output_exceeding_exact_limit():
    collector = OrderedBoundedOutputCollector(byte_limit=3, event_limit=2)

    collector.stdout.write("€")
    assert collector.truncated is False
    collector.stdout.write("x")

    assert collector.events == [
        CapturedStreamEvent("stdout", "€"),
        CapturedStreamEvent("stderr", WORKER_OUTPUT_TRUNCATION_MARKER),
    ]


def test_ordered_output_collector_bounds_event_metadata():
    collector = OrderedBoundedOutputCollector(byte_limit=100, event_limit=2)

    collector.stdout.write("A")
    collector.stderr.write("B")
    collector.stdout.write("C")

    assert collector.payload_bytes == 2
    assert collector.events == [
        CapturedStreamEvent("stdout", "A"),
        CapturedStreamEvent("stderr", "B"),
        CapturedStreamEvent("stderr", WORKER_OUTPUT_TRUNCATION_MARKER),
    ]


def test_ordered_output_collector_validates_configuration_and_writes():
    for kwargs in ({"byte_limit": 0}, {"event_limit": 0}):
        with pytest.raises(ValueError, match="must be positive"):
            OrderedBoundedOutputCollector(**kwargs)

    collector = OrderedBoundedOutputCollector()
    with pytest.raises(TypeError, match="must be str"):
        collector.stdout.write(b"not text")
