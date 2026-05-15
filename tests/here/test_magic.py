from herethere.magic import MagicHere


def test_server_is_started(mocker, tmp_environ):
    tmp_environ["HERE_PORT"] = "0"
    server = mocker.Mock()
    start_server = mocker.patch("herethere.here.magic.start_server")

    def run_coroutine(coroutine):
        coroutine.close()
        return server

    run = mocker.patch("herethere.here.magic.asyncio.run", side_effect=run_coroutine)

    magic = MagicHere(shell=None)
    magic.start_server("tests/here.env")

    start_server.assert_called_once()
    run.assert_called_once()
    assert magic.server is server
