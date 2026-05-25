import logging

import pytest

from herethere.everywhere.config import ConnectionConfigError
from herethere.here.config import ServerConfig

BASE_ENV = {
    "HERE_HOST": "localhost",
    "HERE_PORT": "9022",
    "HERE_USERNAME": "here",
    "HERE_PASSWORD": "there",
    "HERE_KEY_PATH": "tests/key.rsa",
}
BASE_ENV_VALUES = {
    "host": "localhost",
    "port": "9022",
    "username": "here",
    "password": "there",
    "key_path": "tests/key.rsa",
}


def test_server_config_loads_sftp_root():
    env = {**BASE_ENV, "HERE_SFTP_ROOT": "uploads"}

    config = ServerConfig.load_from_dict(env=env, prefix="here")

    assert config.sftp_root == "uploads"


def test_server_config_sftp_root_defaults_to_current_directory():
    config = ServerConfig.load_from_dict(env=BASE_ENV, prefix="here")

    assert config.sftp_root == "."


def test_server_config_loads_deprecated_chroot(caplog):
    env = {**BASE_ENV, "HERE_CHROOT": "legacy-root"}

    with caplog.at_level(logging.WARNING, logger="herethere"):
        config = ServerConfig.load_from_dict(env=env, prefix="here")

    assert config.sftp_root == "legacy-root"
    assert "HERE_CHROOT is deprecated; use HERE_SFTP_ROOT instead" in caplog.text


def test_server_config_prefers_sftp_root_over_deprecated_chroot(caplog):
    env = {
        **BASE_ENV,
        "HERE_SFTP_ROOT": "uploads",
        "HERE_CHROOT": "legacy-root",
    }

    with caplog.at_level(logging.WARNING, logger="herethere"):
        config = ServerConfig.load_from_dict(env=env, prefix="here")

    assert config.sftp_root == "uploads"
    assert "HERE_CHROOT is deprecated and ignored because HERE_SFTP_ROOT is set" in (
        caplog.text
    )


def test_server_config_reports_missing_required_key():
    env = {key: value for key, value in BASE_ENV.items() if key != "HERE_KEY_PATH"}

    with pytest.raises(ConnectionConfigError, match="HERE_KEY_PATH"):
        ServerConfig.load_from_dict(env=env, prefix="here")


def test_server_config_constructor_accepts_deprecated_chroot(caplog):
    with caplog.at_level(logging.WARNING, logger="herethere"):
        config = ServerConfig(**BASE_ENV_VALUES, chroot="legacy-root")

    assert config.sftp_root == "legacy-root"
    assert config.port == 9022
    assert "ServerConfig(chroot=...) is deprecated; use" in caplog.text


def test_server_config_constructor_prefers_sftp_root_over_deprecated_chroot(caplog):
    with caplog.at_level(logging.WARNING, logger="herethere"):
        config = ServerConfig(
            **BASE_ENV_VALUES,
            sftp_root="uploads",
            chroot="legacy-root",
        )

    assert config.sftp_root == "uploads"
    assert "ServerConfig(chroot=...) is deprecated and ignored" in caplog.text
