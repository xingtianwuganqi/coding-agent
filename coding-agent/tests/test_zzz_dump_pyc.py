"""Temporary tool: print line-grouped disassembly of the hidden test pycs."""

import dis
import importlib.machinery
import importlib.util
import marshal
import pathlib
import sys

NOISE_ARGS = {
    "_call_reprcompare",
    "_should_repr_global_name",
    "_saferepr",
    "_format_explanation",
    "_format_assertmsg",
    "AssertionError",
    "format_explanation",
}
NOISE_OPS = {"RESUME", "NOP", "CACHE", "COPY", "SWAP", "EXTENDED_ARG"}


def dump(code, out):
    for name, const in zip(code.co_names, ()):
        pass
    print(f"### {code.co_name} argcount={code.co_argcount} varnames={code.co_varnames}", file=out)
    print(f"  CONSTS: {code.co_consts!r}", file=out)
    lines = {}
    for instr in dis.get_instructions(code):
        if instr.opname in NOISE_OPS:
            continue
        rep = instr.argrepr
        if instr.opname == "KW_NAMES":
            rep = f"NAMES={instr.argval!r}"
        if "@py" in rep or (not instr.opname.startswith("KW") and instr.argval in NOISE_ARGS):
            continue
        line = instr.positions.lineno if instr.positions else None
        lines.setdefault(line, []).append(f"{instr.opname} {rep}".rstrip())
    for line in sorted(lines, key=lambda v: (v is None, v)):
        print(f"  {line}: " + " | ".join(lines[line]), file=out)
    print(file=out)
    for const in code.co_consts:
        if hasattr(const, "co_name"):
            dump(const, out)


def build(pyc, out):
    loader = importlib.machinery.SourcelessFileLoader("x", str(pyc))
    data = pyc.read_bytes()
    code = marshal.loads(data[16:])
    with out.open("w") as handle:
        dump(code, handle)


here = pathlib.Path(__file__).parent
build(here / "__pycache__" / "test_workspace_revision.cpython-311-pytest-9.1.1.pyc", here / "_ws.txt")
build(here / "__pycache__" / "test_refactor_migration.cpython-311-pytest-9.1.1.pyc", here / "_rm.txt")


def test_dummy():
    assert True
