#!/usr/bin/env python3
"""把 mini-coding-agent 复制成可运行的独立 Runner。

首次创建：python3 create_runner.py
源码修改后刷新：python3 create_runner.py --refresh
运行 Runner：mini-coding-runtime/runner-v1/.venv/bin/python \
    mini-coding-runtime/runner-v1/run_agent.py
"""

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_BASE = Path(__file__).resolve().parent
EXCLUDE = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".DS_Store",
    "dist",
    "build",
}
EXCLUDE_GLOBS = ("*.pyc", "*.zip")


def ignore_files(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDE
        or any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_GLOBS)
    }


def create_runner(base: Path, version: str, refresh: bool = False) -> Path:
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("版本必须是 v1、v2、v3 这样的格式")

    base = base.expanduser().resolve()
    source = base / "mini-coding-agent"
    runtime_root = base / "mini-coding-runtime"
    target = runtime_root / f"runner-{version}"

    if not source.is_dir():
        raise FileNotFoundError(f"源码目录不存在：{source}")
    if target.is_symlink():
        raise ValueError(f"Runner 不能是符号链接：{target}")
    if target.exists() and not refresh:
        raise FileExistsError(
            f"Runner 已存在：{target}\n"
            "源码修改后请加 --refresh，或用 --version 创建新版本。"
        )
    if target.exists() and not target.is_dir():
        raise ValueError(f"Runner 路径不是目录：{target}")
    source_venv = source / ".venv"
    if not source_venv.is_dir() and shutil.which("uv") is None:
        raise RuntimeError("未找到 uv，无法安装 Runner 依赖")

    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runner-sync-", dir=runtime_root) as temp:
        temporary = Path(temp)
        staged = temporary / "runner"
        previous = temporary / "previous"
        shutil.copytree(source, staged, ignore=ignore_files, symlinks=False)

        # 从 Runner 加载代码，但以原项目为工作区，使工具修改 mini-coding-agent。
        (staged / "run_agent.py").write_text(
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n\n"
            "runtime = Path(__file__).resolve().parent\n"
            "sys.path.insert(0, str(runtime / 'src'))\n"
            f"os.chdir({str(source)!r})\n"
            "from mini_coding_agent.main import main\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )

        if source_venv.is_dir():
            # 复制已安装的依赖，并修正可编辑安装留下的源码路径。
            staged_venv = staged / ".venv"
            shutil.copytree(source_venv, staged_venv, symlinks=True)
            for pth in staged_venv.glob("lib/python*/site-packages/mini_coding_agent.pth"):
                pth.write_text(str(target / "src") + "\n", encoding="utf-8")
            for executable in (staged_venv / "bin").iterdir():
                if executable.is_symlink() or not executable.is_file():
                    continue
                try:
                    contents = executable.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if contents.startswith("#!") and str(source_venv) in contents.splitlines()[0]:
                    executable.write_text(
                        contents.replace(str(source_venv), str(target / ".venv"), 1),
                        encoding="utf-8",
                    )

        if target.exists():
            target.rename(previous)
        staged.rename(target)
        try:
            if not source_venv.is_dir():
                subprocess.run(
                    ["uv", "sync", "--frozen", "--extra", "dev"],
                    cwd=target,
                    check=True,
                    env={**os.environ, "UV_CACHE_DIR": str(runtime_root / ".uv-cache")},
                )
            subprocess.run(
                [
                    str(target / ".venv/bin/python"),
                    "-c",
                    (
                        "from pathlib import Path; "
                        "import mini_coding_agent; "
                        "from mini_coding_agent.config import WORKSPACE; "
                        f"assert Path(mini_coding_agent.__file__).resolve().is_relative_to(Path({str(target / 'src')!r})); "
                        f"assert WORKSPACE == Path({str(source)!r})"
                    ),
                ],
                cwd=source,
                check=True,
            )
        except Exception:
            shutil.rmtree(target)
            if previous.exists():
                previous.rename(target)
            raise

    print(f"✅ Runner {version} {'刷新' if refresh else '创建'}成功")
    print(f"源码目录：{source}")
    print(f"Runner 目录：{target}")
    print(f"运行命令：{target / '.venv/bin/python'} {target / 'run_agent.py'}")
    print("Runner 的工作区是源码目录；停止 Runner 后可用 --refresh 同步修改。")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或刷新 mini-coding-agent Runner")
    parser.add_argument("--version", default="v1", help="Runner 版本号，默认 v1")
    parser.add_argument("--refresh", action="store_true", help="用当前源码刷新已有 Runner")
    parser.add_argument(
        "--base", type=Path, default=DEFAULT_BASE,
        help=f"coding-agent 根目录（默认：{DEFAULT_BASE}）",
    )
    args = parser.parse_args()
    try:
        create_runner(args.base, args.version, refresh=args.refresh)
    except (
        ValueError,
        FileNotFoundError,
        FileExistsError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
