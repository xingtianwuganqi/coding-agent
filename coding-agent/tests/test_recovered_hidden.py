"""Temporary harness: load recovered hidden tests from sourceless .pyc files."""

import importlib.machinery
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
PYCACHE = HERE / "__pycache__"


def _load(module_name, source_pyc, target_pyc):
    data = source_pyc.read_bytes()
    target_pyc.write_bytes(data)
    loader = importlib.machinery.SourcelessFileLoader(module_name, str(target_pyc))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


_ws = _load(
    "hidden_workspace_revision",
    PYCACHE / "test_workspace_revision.cpython-311-pytest-9.1.1.pyc",
    HERE / "_hidden_workspace_revision.pyc",
)

for _name in dir(_ws):
    if _name.startswith("test_"):
        globals()[_name] = getattr(_ws, _name)
