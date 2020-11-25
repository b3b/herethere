from os import environ

import pytest


@pytest.fixture
def tmp_environ(mocker):
    mocker.patch.dict('os.environ', {}, clear=True)
    return environ
