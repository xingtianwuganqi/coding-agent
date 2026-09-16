"""Filesystem tools restricted to the workspace."""

from pathlib import Path
from ..config import (
    DEFAULT_READ_LINES,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    WORKSPACE,
)
from ..task_state import FileOperationResult

# 解析路径
def resolve_path(path: str) -> Path:
    """Resolve a path relative to the workspace."""
    target = (WORKSPACE / path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"Path {path} is outside the workspace.")
    return target


# 
def list_files(path: str) -> str:
    target = resolve_path(path)
    if not target.exists():
        return f"Directory does not exist: {path}"

    if not target.is_dir():
        return f"Path is not a directory: {path}"

    result = []

    for child in sorted(target.iterdir()):
        relative = child.relative_to(WORKSPACE)
        if child.is_dir():
            result.append(f"{relative}/")
        else:
            result.append(str(relative))

    return "\n".join(result) or "(empty directory)"


# 读取文件
def read_file(
        path: str, 
        start_line: int = 1, 
        end_line: int | None = None
) -> str:
    target = resolve_path(path)
    if not target.exists():
        return f"File does not exist: {path}"
    if not target.is_file():
        return f"Path is not a file: {path}"
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (
            f"Cannot read binary or"
            f"non-UTF-8 file:{path}"
        )

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return (
            f"File: {path}\n"
            f"(empty file)"
        )

    if start_line < 1:
        start_line = 1

    if start_line > total_lines:
        return (
            f"Start line {start_line} "
            f"is beyond the end of file.\n"
            f"Total lines: {total_lines}"
        )

    if end_line is None:
        end_line = (
            start_line 
            + DEFAULT_READ_LINES
            - 1
        )

    end_line= min(end_line, total_lines)

    requested_lines = (
        end_line 
        - start_line
        + 1
    )

    if requested_lines > MAX_READ_LINES:
        end_line = (
            start_line
            + MAX_READ_LINES
            - 1
        )

        end_line = min(
            end_line,
            total_lines,
        )

    selected_lines = lines[
        start_line - 1:end_line
    ]

    numbered_lines = []

    for line_number, line in enumerate(
        selected_lines, start=start_line
    ):
        numbered_lines.append(
            f"{line_number:4}:{line}"
        )

    result = [
        f"file: {path}",
        (
            f"Lines {start_line}-{end_line}"
            f"of {total_lines}"
        ),
        "",
        "\n".join(numbered_lines),
    ]

    if end_line < total_lines:
        result.append("")
        result.append(
            (
                "More lines are available. "
                f"Continue from line "
                f"{end_line + 1} if needed."
            )
        )

    return "\n".join(result)


def search_text(
        query: str,
        path: str = "."
) -> str:
    target = resolve_path(path)

    if not target.exists():
        return (
            f"Path does not exist: {path}"
        )

    files = []

    if target.is_file():
        files.append(target)

    else:
        for file in target.rglob("*"):
            if file.is_file():
                files.append(file)

    results = []
    for file in files:
        try: 
            content = file.read_text(
                encoding="utf-8"
            )
        except (UnicodeDecodeError, PermissionError):
            continue

        for line_number, line in enumerate(
            content.splitlines(),
            start=1
        ):
            if query.lower() in line.lower():
                relative = file.relative_to(
                    WORKSPACE
                )

                results.append(
                    (
                        f"{relative}:"
                        f"{line_number}: "
                    )
                )

                if (
                    len(results)
                    >= MAX_SEARCH_RESULTS
                ):
                    return (
                        "\n".join(results)
                        + "\n\n"
                        + "[Search results truncated]"
                    )

    if not results:
        return (
            f"No matches found for: "
            f"{query}"
        )

    return "\n".join(results)


# 写入文件
def write_file(path: str, content: str) -> FileOperationResult:
    target = resolve_path(path)

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        old_content = target.read_text(
            encoding="utf-8"
        )
        if old_content == content:
            return FileOperationResult(
                success=True,
                changed=False,
                message=(
                    f"No changes needed: {path}"
                )
            )
    target.write_text(content, encoding="utf-8")
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully wrote file: {path}"
        )
    )


# 写入text
def replace_text(path: str, old_text: str, new_text: str) -> FileOperationResult:
    target = resolve_path(path)
    if not target.exists():
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"File does not exist: {path}"
            )
        )
    if not target.is_file():
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Path is not a file: {path}"
            )
        )

    content = target.read_text(encoding="utf-8")
    if old_text not in content:
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Text '{old_text}' not found in file: {path}"
            )
        )

    count = content.count(old_text)
    if count > 1:
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Old text appears {count} times. "
                "Please provide more surrounding context "
                "so the replacement is unambiguous."
            )
        )

    updated_content = content.replace(old_text, new_text, 1)
    if updated_content == content:
        return FileOperationResult(
            success=True,
            changed=False,
            message=(
                f"No changes needed: {path}"
            )
        )
    target.write_text(updated_content, encoding="utf-8")
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully updated file: "
            f"{path}"
        )
    )


def create_directory(
        path: str,
) -> FileOperationResult:
    target = resolve_path(path)
    if target.exists():
        if target.is_dir():
            return FileOperationResult(
                success=True,
                changed=False,
                message=f"Directory already exists: {path}"
            )

        return FileOperationResult(
            success=False,
            changed=False,
            message=f"Path already exists and is not a direcotry: {path}"
        )

    target.mkdir(
        parents=True,
        exist_ok=True,
    )
    return FileOperationResult(
        success=True,
        changed=True,
        message=f"Successfully created direcotry: {path}"
    )
    