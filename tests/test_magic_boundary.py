import builtins
import importlib
import sys
import types

from herethere import magic


def _fail_on_patcher_import(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "nest_asyncio2":
            raise AssertionError("nest_asyncio2 should not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_everywhere_magic_import_does_not_import_patcher(monkeypatch):
    _fail_on_patcher_import(monkeypatch)
    sys.modules.pop("herethere.everywhere.magic", None)

    importlib.import_module("herethere.everywhere.magic")


def test_shell_none_construction_does_not_import_patcher(monkeypatch):
    _fail_on_patcher_import(monkeypatch)

    from herethere.here.magic import MagicHere  # noqa: PLC0415

    MagicHere(shell=None)


def test_fake_shell_construction_does_not_import_patcher(monkeypatch):
    _fail_on_patcher_import(monkeypatch)

    from herethere.here.magic import MagicHere  # noqa: PLC0415

    MagicHere(shell=object())


def test_real_ipython_shell_construction_does_not_import_patcher(monkeypatch, tmp_path):
    from IPython.core.interactiveshell import InteractiveShell  # noqa: PLC0415

    _fail_on_patcher_import(monkeypatch)
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path))

    from herethere.here.magic import MagicHere  # noqa: PLC0415

    MagicHere(shell=InteractiveShell())


def test_load_ipython_extension_registers_magics(mocker):
    from herethere.there import local_commands  # noqa: PLC0415

    ipython = types.SimpleNamespace(register_magics=mocker.Mock())
    original = dict(local_commands._LOCAL_THERE_HANDLERS)
    local_commands._LOCAL_THERE_HANDLERS.clear()

    try:
        magic.load_ipython_extension(ipython)

        first_magic = ipython.register_magics.call_args_list[0].args[0]
        second_magic = ipython.register_magics.call_args_list[1].args[0]

        assert ipython.register_magics.call_count == 2
        assert isinstance(first_magic, magic.MagicHere)
        assert isinstance(second_magic, magic.MagicThere)
        assert "ai" in local_commands._LOCAL_THERE_HANDLERS
        assert "generate" not in local_commands._LOCAL_THERE_HANDLERS
        assert "gen" not in local_commands._LOCAL_THERE_HANDLERS
    finally:
        local_commands._LOCAL_THERE_HANDLERS.clear()
        local_commands._LOCAL_THERE_HANDLERS.update(original)
