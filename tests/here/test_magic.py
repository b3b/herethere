from herethere.magic import MagicHere


def test_server_is_started(mocker, tmp_environ):
    tmp_environ["HERE_PORT"] = "0"
    server = mocker.Mock()
    start_server = mocker.patch(
        "herethere.here.magic.start_server",
        new=mocker.Mock(return_value="start-server-result"),
    )

    run = mocker.patch("herethere.here.magic.run_sync", return_value=server)

    magic = MagicHere(shell=None)
    magic.start_server("tests/here.env")

    start_server.assert_called_once()
    run.assert_called_once_with("start-server-result")
    assert magic.server is server
