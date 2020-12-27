import sys
from herethere.everywhere.redirected_output import RedirectedOutputWrapper, redirect_output


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
        assert list(sys.stdout._redirected_streams.values()) == [new_stdout]
        assert list(sys.stderr._redirected_streams.values()) == [new_stderr]

    captured = capfd.readouterr()
    assert not captured.out
    assert not captured.err

    new_stdout.write.assert_called_once_with("test out")
    new_stderr.write.assert_called_once_with("test err")
    assert list(sys.stdout._redirected_streams.values()) == []
    assert list(sys.stderr._redirected_streams.values()) == []


def test_use_non_redirected_output(mocker, capfd):
    with redirect_output(mocker.Mock(), mocker.Mock()):
        pass

    sys.stdout.write("test out")
    sys.stderr.write("test err")

    captured = capfd.readouterr()
    assert captured.out == "test out"
    assert captured.err == "test err"
