import builtins
import importlib
import sys
import types

from IPython.core.interactiveshell import InteractiveShell

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


def test_real_ipython_shell_construction_applies_patcher(monkeypatch, mocker, tmp_path):
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path))
    patcher = types.SimpleNamespace(apply=mocker.Mock())
    monkeypatch.setitem(sys.modules, "nest_asyncio2", patcher)

    from herethere.here.magic import MagicHere  # noqa: PLC0415

    MagicHere(shell=InteractiveShell())

    patcher.apply.assert_called_once_with()


def test_load_ipython_extension_registers_magics(mocker):
    ipython = types.SimpleNamespace(register_magics=mocker.Mock())

    magic.load_ipython_extension(ipython)

    first_magic = ipython.register_magics.call_args_list[0].args[0]
    second_magic = ipython.register_magics.call_args_list[1].args[0]

    assert ipython.register_magics.call_count == 2
    assert isinstance(first_magic, magic.MagicHere)
    assert isinstance(second_magic, magic.MagicThere)
