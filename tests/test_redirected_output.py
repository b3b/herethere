import asyncio
import sys

from herethere.everywhere.redirected_output import (
    RedirectedOutputWrapper,
    redirect_output,
)


def test_redirected_output_installed(mocker):
    assert not isinstance(sys.stdout, RedirectedOutputWrapper)
    assert not isinstance(sys.stderr, RedirectedOutputWrapper)

    with redirect_output(mocker.Mock(), mocker.Mock()):
        pass

    stdout, stderr = sys.stdout, sys.stderr
    assert isinstance(sys.stdout, RedirectedOutputWrapper)
    assert isinstance(sys.stderr, RedirectedOutputWrapper)

    with redirect_output(mocker.Mock(), mocker.Mock()):
        pass

    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_output_redirected(mocker, capfd):
    assert not isinstance(sys.stdout, RedirectedOutputWrapper)
    assert not isinstance(sys.stderr, RedirectedOutputWrapper)
    new_stdout = mocker.Mock()
    new_stderr = mocker.Mock()

    with redirect_output(new_stdout, new_stderr):
        assert sys.stdout.write("test out")
        assert sys.stderr.write("test err")
        assert sys.stdout._target_stream is new_stdout
        assert sys.stderr._target_stream is new_stderr

    captured = capfd.readouterr()
    assert not captured.out
    assert not captured.err

    new_stdout.write.assert_called_once_with("test out")
    new_stderr.write.assert_called_once_with("test err")
    assert sys.stdout._target_stream is sys.stdout._original_stream
    assert sys.stderr._target_stream is sys.stderr._original_stream


def test_nested_redirect_restores_outer_writer():
    outer = RedirectedOutputWrapper(sys.stdout)
    outer_writer = _RecordingWriter()
    inner_writer = _RecordingWriter()

    outer.register(outer_writer)
    outer.write("outer-before")
    outer.register(inner_writer)
    outer.write("inner")
    outer.unregister()
    outer.write("outer-after")
    outer.unregister()

    assert outer_writer.written == "outer-beforeouter-after"
    assert inner_writer.written == "inner"


class _RecordingWriter:
    def __init__(self):
        self.written = ""

    def write(self, data):
        self.written += data
        return len(data)


def test_concurrent_asyncio_redirects_are_task_local():
    first_writer = _RecordingWriter()
    second_writer = _RecordingWriter()

    async def run():
        first_registered = asyncio.Event()
        second_finished = asyncio.Event()

        async def first():
            with redirect_output(first_writer, first_writer):
                first_registered.set()
                await second_finished.wait()
                sys.stdout.write("first")

        async def second():
            await first_registered.wait()
            with redirect_output(second_writer, second_writer):
                sys.stdout.write("second")
            second_finished.set()

        await asyncio.gather(first(), second())

    asyncio.run(run())

    assert first_writer.written == "first"
    assert second_writer.written == "second"


def test_use_non_redirected_output(mocker, capfd):
    with redirect_output(mocker.Mock(), mocker.Mock()):
        pass

    sys.stdout.write("test out")
    sys.stderr.write("test err")

    captured = capfd.readouterr()
    assert captured.out == "test out"
    assert captured.err == "test err"


def test_cleanup_ignored_when_standard_streams_replaced(mocker):
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        with redirect_output(mocker.Mock(), mocker.Mock()):
            sys.stdout = mocker.Mock()
            sys.stderr = mocker.Mock()
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr


def test_flush_ignored_when_target_has_no_flush():
    wrapper = RedirectedOutputWrapper(sys.stdout)

    class StreamWithoutFlush:
        pass

    wrapper.register(StreamWithoutFlush())
    wrapper.flush()
    wrapper.unregister()


def test_flush_delegated_to_target(mocker):
    wrapper = RedirectedOutputWrapper(sys.stdout)
    target = mocker.Mock()

    wrapper.register(target)
    wrapper.flush()
    wrapper.unregister()

    target.flush.assert_called_once_with()


def test_unregister_without_redirect_is_a_noop():
    wrapper = RedirectedOutputWrapper(sys.stdout)

    wrapper.unregister()

    assert wrapper._target_stream is sys.stdout
