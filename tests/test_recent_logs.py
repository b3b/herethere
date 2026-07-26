import logging

import pytest

from herethere.everywhere.recent_logs import (
    RECENT_LOGS_FORMAT,
    RECENT_LOGS_PROTOCOL_VERSION,
    RECENT_LOGS_RESPONSE_TYPE,
    RecentLogHandler,
    create_recent_log_handler,
)


def log_record(message):
    return logging.LogRecord(
        name="example",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_records": 0}, "max_records"),
        ({"max_bytes": 0}, "max_bytes"),
    ],
)
def test_recent_log_handler_validates_bounds(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RecentLogHandler(**kwargs)


def test_recent_log_handler_returns_ordered_snapshot_and_wire_data():
    handler = RecentLogHandler(max_records=3, max_bytes=100)
    handler.setFormatter(logging.Formatter("%(message)s"))

    handler.handle(log_record("first"))
    handler.handle(log_record("second"))

    snapshot = handler.snapshot()
    assert snapshot.text == "first\nsecond\n"
    assert snapshot.bytes == 13
    assert snapshot.records == 2
    assert snapshot.truncated is False
    assert snapshot.asdict() == {
        "type": RECENT_LOGS_RESPONSE_TYPE,
        "version": RECENT_LOGS_PROTOCOL_VERSION,
        "text": "first\nsecond\n",
        "bytes": 13,
        "records": 2,
        "truncated": False,
    }


def test_recent_log_handler_evicts_oldest_records_by_count():
    handler = RecentLogHandler(max_records=2, max_bytes=100)
    handler.setFormatter(logging.Formatter("%(message)s"))

    for message in ("first", "second", "third"):
        handler.handle(log_record(message))

    snapshot = handler.snapshot()
    assert snapshot.text == "second\nthird\n"
    assert snapshot.truncated is True


def test_recent_log_handler_limits_snapshot_to_newest_records():
    handler = RecentLogHandler(max_records=3, max_bytes=100)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for message in ("first", "second", "third"):
        handler.handle(log_record(message))

    snapshot = handler.snapshot(max_records=2)

    assert snapshot.text == "second\nthird\n"
    assert snapshot.records == 2
    assert snapshot.truncated is False


@pytest.mark.parametrize("max_records", [0, 4, True, "2"])
def test_recent_log_handler_validates_snapshot_record_limit(max_records):
    handler = RecentLogHandler(max_records=3)

    with pytest.raises(ValueError, match="range 1..3"):
        handler.snapshot(max_records=max_records)


def test_recent_log_handler_evicts_oldest_records_by_bytes():
    handler = RecentLogHandler(max_records=10, max_bytes=8)
    handler.setFormatter(logging.Formatter("%(message)s"))

    handler.handle(log_record("1234"))
    handler.handle(log_record("5678"))

    snapshot = handler.snapshot()
    assert snapshot.text == "5678\n"
    assert snapshot.bytes <= handler.max_bytes
    assert snapshot.truncated is True


def test_recent_log_handler_bounds_one_oversized_utf8_record():
    handler = RecentLogHandler(max_records=10, max_bytes=5)
    handler.setFormatter(logging.Formatter("%(message)s"))

    handler.handle(log_record("x€€"))

    snapshot = handler.snapshot()
    assert snapshot.text == "€\n"
    assert snapshot.bytes == 4
    assert snapshot.truncated is True


def test_recent_log_handler_reports_formatting_errors(mocker):
    handler = RecentLogHandler()
    formatter = mocker.Mock()
    formatter.format.side_effect = ValueError("bad")
    handler.setFormatter(formatter)
    handle_error = mocker.patch.object(handler, "handleError")
    record = log_record("message")

    handler.handle(record)

    handle_error.assert_called_once_with(record)


def test_recent_log_handler_factory_uses_shared_format():
    handler = create_recent_log_handler(max_records=2, max_bytes=1000)

    assert handler.max_records == 2
    assert handler.max_bytes == 1000
    assert handler.formatter._fmt == RECENT_LOGS_FORMAT
