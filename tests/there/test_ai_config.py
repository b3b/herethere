import pytest

from herethere.there.ai.config import (
    AIConfig,
    AIConfigError,
    clear_ai_config_path,
    get_ai_config,
    set_ai_config_path,
)


@pytest.fixture(autouse=True)
def reset_ai_config_path():
    clear_ai_config_path()
    yield
    clear_ai_config_path()


def test_missing_model_raises_friendly_error(tmp_environ):
    with pytest.raises(AIConfigError, match="Set THERE_AI_MODEL"):
        get_ai_config()


def test_default_config_values(tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"

    config = get_ai_config()

    assert config.base_url == "https://api.openai.com/v1"
    assert config.model == "test-model"
    assert config.api_key == ""
    assert config.temperature == 0.2
    assert config.timeout == 300


def test_custom_config_values(tmp_environ):
    tmp_environ.update(
        {
            "THERE_AI_BASE_URL": "http://localhost:11434/v1/",
            "THERE_AI_MODEL": "qwen",
            "THERE_AI_API_KEY": "secret",
            "THERE_AI_TEMPERATURE": "0.7",
            "THERE_AI_TIMEOUT": "45",
            "THERE_AI_PROMPTS": "kivy, midi",
        }
    )

    config = get_ai_config()

    assert config.base_url == "http://localhost:11434/v1"
    assert config.model == "qwen"
    assert config.api_key == "secret"
    assert config.temperature == 0.7
    assert config.timeout == 45
    assert config.prompts == ("kivy", "midi")


def test_config_loads_dotenv_file(tmp_path, tmp_environ):
    path = tmp_path / "there_ai.env"
    path.write_text(
        "\n".join(
            [
                "THERE_AI_MODEL=file-model",
                "THERE_AI_API_KEY=file-secret",
                "THERE_AI_TIMEOUT=60",
            ]
        ),
        encoding="utf-8",
    )

    config = AIConfig.load(path=path)

    assert config.model == "file-model"
    assert config.api_key == "file-secret"
    assert config.timeout == 60


def test_environment_overrides_dotenv_file(tmp_path, tmp_environ):
    path = tmp_path / "there_ai.env"
    path.write_text("THERE_AI_MODEL=file-model\n", encoding="utf-8")
    tmp_environ["THERE_AI_MODEL"] = "env-model"

    config = AIConfig.load(path=path)

    assert config.model == "env-model"


def test_session_config_path_switches_config_file(tmp_path, tmp_environ):
    openai_path = tmp_path / "there_ai.openai.env"
    deepseek_path = tmp_path / "there_ai.deepseek.env"
    openai_path.write_text("THERE_AI_MODEL=openai-model\n", encoding="utf-8")
    deepseek_path.write_text("THERE_AI_MODEL=deepseek-model\n", encoding="utf-8")

    set_ai_config_path(str(openai_path))
    assert get_ai_config().model == "openai-model"

    set_ai_config_path(str(deepseek_path))
    assert get_ai_config().model == "deepseek-model"


def test_clear_session_config_path_restores_default_discovery(tmp_path, tmp_environ):
    custom_path = tmp_path / "there_ai.custom.env"
    custom_path.write_text("THERE_AI_MODEL=custom-model\n", encoding="utf-8")

    set_ai_config_path(str(custom_path))
    assert get_ai_config().model == "custom-model"

    clear_ai_config_path()
    with pytest.raises(AIConfigError, match="Set THERE_AI_MODEL"):
        get_ai_config()


def test_empty_session_config_path_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        set_ai_config_path(" ")


def test_missing_session_config_path_raises(tmp_path):
    with pytest.raises(AIConfigError, match="AI config file does not exist"):
        set_ai_config_path(str(tmp_path / "missing.env"))


def test_session_config_path_rejects_directory(tmp_path):
    with pytest.raises(AIConfigError, match="AI config file does not exist"):
        set_ai_config_path(str(tmp_path))


def test_invalid_temperature_raises(tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    tmp_environ["THERE_AI_TEMPERATURE"] = "warm"

    with pytest.raises(AIConfigError, match="must be a number"):
        get_ai_config()


def test_invalid_timeout_raises(tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    tmp_environ["THERE_AI_TIMEOUT"] = "slow"

    with pytest.raises(AIConfigError, match="THERE_AI_TIMEOUT must be a number"):
        get_ai_config()


def test_non_positive_timeout_raises(tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    tmp_environ["THERE_AI_TIMEOUT"] = "0"

    with pytest.raises(AIConfigError, match="must be greater than 0"):
        get_ai_config()
