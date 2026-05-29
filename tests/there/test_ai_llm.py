import urllib.error

import pytest

from herethere.there.ai.config import AIConfig
from herethere.there.ai.llm import AIProviderError, call_openai_compatible


def config(**overrides):
    values = {
        "base_url": "http://example.test/v1",
        "model": "test-model",
        "api_key": "",
        "temperature": 0.2,
        "timeout": 45,
    }
    values.update(overrides)
    return AIConfig(**values)


def response_with(body, mocker):
    response = mocker.Mock()
    response.__enter__ = mocker.Mock(return_value=response)
    response.__exit__ = mocker.Mock(return_value=None)
    response.read.return_value = body
    return response


def test_call_openai_compatible_passes_configured_timeout(mocker):
    response = response_with(
        b'{"choices":[{"message":{"content":"print(1)"}}]}',
        mocker,
    )
    urlopen = mocker.patch("herethere.there.ai.llm.urllib.request.urlopen")
    urlopen.return_value = response

    result = call_openai_compatible(
        [{"role": "user", "content": "hi"}],
        config(api_key="secret"),
    )

    assert result == "print(1)"

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://example.test/v1/chat/completions"
    assert urlopen.call_args.kwargs["timeout"] == 45


def test_call_openai_compatible_reports_read_timeout(mocker):
    response = mocker.Mock()
    response.__enter__ = mocker.Mock(return_value=response)
    response.__exit__ = mocker.Mock(return_value=None)
    response.read.side_effect = TimeoutError("The read operation timed out")
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        return_value=response,
    )

    with pytest.raises(AIProviderError, match="timed out after 45 seconds"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())


def test_call_openai_compatible_reports_http_error(mocker):
    error = urllib.error.HTTPError(
        "http://example.test/v1/chat/completions",
        500,
        "server error",
        hdrs=None,
        fp=response_with(b"provider broke", mocker),
    )
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        side_effect=error,
    )

    with pytest.raises(AIProviderError, match="HTTP 500: provider broke"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())


def test_call_openai_compatible_reports_url_error(mocker):
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    )

    with pytest.raises(AIProviderError, match="connection refused"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())


def test_call_openai_compatible_reports_invalid_json(mocker):
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        return_value=response_with(b"not json", mocker),
    )

    with pytest.raises(AIProviderError, match="invalid JSON"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())


def test_call_openai_compatible_reports_missing_content(mocker):
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        return_value=response_with(b'{"choices":[]}', mocker),
    )

    with pytest.raises(AIProviderError, match=r"choices\[0\]\.message\.content"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())


@pytest.mark.parametrize(
    "body",
    (
        b'{"choices":[{"message":{"content":""}}]}',
        b'{"choices":[{"message":{"content":null}}]}',
    ),
)
def test_call_openai_compatible_reports_empty_content(mocker, body):
    mocker.patch(
        "herethere.there.ai.llm.urllib.request.urlopen",
        return_value=response_with(body, mocker),
    )

    with pytest.raises(AIProviderError, match="empty content"):
        call_openai_compatible([{"role": "user", "content": "hi"}], config())
