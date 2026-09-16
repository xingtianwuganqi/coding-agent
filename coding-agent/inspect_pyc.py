import marshal
import sys


def load(path):
    with open(path, "rb") as f:
        f.read(16)
        return marshal.load(f)


def walk(code, depth=0):
    for const in code.co_consts:
        if hasattr(const, "co_name"):
            print("  " * depth + const.co_name)
            walk(const, depth + 1)


for path in sys.argv[1:]:
    print("====", path)
    walk(load(path))
