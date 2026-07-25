import json
from io import StringIO

from herethere.everywhere.live import (
    MAX_TRACEBACK_BYTES,
    bounded_utf8_tail,
    execute_live,
)
from herethere.everywhere.protocol import EventStream, write_event


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
