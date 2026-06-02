import pytest

from herethere.there.ai import prompts


@pytest.fixture(autouse=True)
def reset_ai_prompts():
    original_active_prompts = prompts._ai_prompt_store.active_prompts
    original_registry = dict(prompts._ai_prompt_store.registry)
    prompts.reset_ai_prompt_store()
    yield
    prompts._ai_prompt_store.active_prompts = original_active_prompts
    prompts._ai_prompt_store.registry.clear()
    prompts._ai_prompt_store.registry.update(original_registry)


def test_build_messages_uses_default_template(tmp_environ):
    messages = prompts.build_messages("print status")

    assert messages[0]["role"] == "system"
    assert "live, already-running Python application process" in messages[0]["content"]
    assert "terminating the host process or app" in messages[0]["content"]
    assert "sys.exit()" in messages[0]["content"]
    assert "raise SystemExit" in messages[0]["content"]
    assert prompts.DEFAULT_AI_TEMPLATE_RESOURCE == "prompts/default.md"
    assert messages[1]["role"] == "user"
    assert "print status" in messages[1]["content"]


def test_build_messages_is_exported_from_ai_package():
    namespace = {}

    exec("from herethere.there.ai import *", namespace)

    assert namespace["build_messages"] is prompts.build_messages


def test_builtin_fix_prompt_is_registered(tmp_environ):
    assert "previously executed %%there Python cell" in prompts.get_ai_prompt("fix")
    assert prompts.FIX_AI_TEMPLATE_RESOURCE == "prompts/fix.md"


def test_list_ai_prompts_returns_registered_prompt_names():
    prompts.register_ai_prompt("kivy", "kivy rules")

    assert prompts.list_ai_prompts() == ("default", "fix", "kivy")


def test_registered_prompt_can_override_default(tmp_environ):
    prompts.register_ai_prompt("default", "custom default")

    assert prompts.get_ai_template() == "custom default"


def test_registered_prompt_can_override_fix(tmp_environ):
    prompts.register_ai_prompt("fix", "custom fix")

    assert prompts.get_ai_template(["fix"]) == "custom fix"


def test_empty_registered_prompt_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        prompts.register_ai_prompt("custom", " ")


def test_empty_prompt_name_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        prompts.register_ai_prompt(" ", "custom rules")


def test_registered_prompt_sections_are_composed_in_order(tmp_environ):
    prompts.register_ai_prompt("kivy", "kivy rules")
    prompts.register_ai_prompt("midi", "midi rules")

    template = prompts.get_ai_template(["kivy", "midi"])

    assert template.index("kivy rules") < template.index("midi rules")


def test_get_ai_template_uses_exact_requested_prompts(tmp_environ):
    prompts.register_ai_prompt("custom", "custom rules")

    assert prompts.get_ai_template(["custom"]) == "custom rules"


def test_prompt_names_are_deduped_in_order(tmp_environ):
    prompts.register_ai_prompt("kivy", "kivy rules")

    template = prompts.build_ai_template(["default", "kivy", "kivy"])

    assert template.count("kivy rules") == 1


def test_unknown_prompt_raises_friendly_error(tmp_environ):
    with pytest.raises(prompts.AIPromptError, match="Unknown .*'missing'"):
        prompts.get_ai_template(["missing"])


def test_session_prompt_stack(tmp_environ):
    prompts.register_ai_prompt("kivy", "kivy rules")
    prompts.set_ai_prompts("kivy")

    assert "kivy rules" in prompts.get_ai_template()


def test_session_prompt_stack_splits_comma_separated_names(tmp_environ):
    prompts.register_ai_prompt("kivy", "kivy rules")
    prompts.register_ai_prompt("midi", "midi rules")

    prompts.set_ai_prompts("kivy,, midi")

    template = prompts.get_ai_template()
    assert "kivy rules" in template
    assert "midi rules" in template


def test_session_prompt_stack_can_replace_default(tmp_environ):
    prompts.register_ai_prompt("custom", "custom rules")
    prompts.set_ai_prompts("custom")

    assert prompts.get_ai_template() == "custom rules"


def test_clear_ai_prompts_restores_default_template(tmp_environ):
    prompts.register_ai_prompt("custom", "custom rules")
    prompts.set_ai_prompts("custom")

    prompts.clear_ai_prompts()

    assert (
        "live, already-running Python application process" in prompts.get_ai_template()
    )


def test_empty_prompt_stack_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        prompts.build_ai_prompt_names([])


def test_prompt_option_resolver_appends_explicit_prompts_to_session_prompts(
    tmp_environ,
):
    prompts.set_ai_prompts("session")

    assert prompts.resolve_ai_prompt_options(
        ["explicit"],
        config_prompt_names=["config"],
    ) == ("session", "explicit")


def test_prompt_option_resolver_keeps_runtime_prompts_when_explicit_prompt_is_added(
    tmp_environ,
):
    prompts.set_ai_prompts(
        "default",
        "kivy-runtime",
        "kivy-kv",
        "android-runtime",
        "jnius",
        "android-permissions",
        "android-packages",
        "android-media",
        "plyer",
    )

    assert prompts.resolve_ai_prompt_options(["midi"]) == (
        "default",
        "kivy-runtime",
        "kivy-kv",
        "android-runtime",
        "jnius",
        "android-permissions",
        "android-packages",
        "android-media",
        "plyer",
        "midi",
    )


def test_prompt_option_resolver_uses_session_before_config(tmp_environ):
    prompts.set_ai_prompts("session")

    assert prompts.resolve_ai_prompt_options(
        config_prompt_names=["config"],
    ) == ("session",)


def test_prompt_option_resolver_uses_exact_config_prompts(tmp_environ):
    assert prompts.resolve_ai_prompt_options(config_prompt_names=["config"]) == (
        "config",
    )


def test_prompt_option_resolver_uses_exact_explicit_prompts(tmp_environ):
    assert prompts.resolve_ai_prompt_options(["midi"]) == ("midi",)


def test_prompt_option_resolver_falls_back_to_default(tmp_environ):
    assert prompts.resolve_ai_prompt_options() == ("default",)
