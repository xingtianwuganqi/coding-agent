"""Tool implementations: filesystem reads/writes and shell execution."""

import shlex
import subprocess
from pathlib import Path
from .tool_result import ToolResult

from .config import (
    WORKSPACE,
    DEFAULT_READ_LINES,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
)
from .context import truncate_text
from .evidence import (
    CommandResult,
    FileOperationResult,
)
from .permission import (
    Permission,
    classify_command,
    ask_for_confirmation,
)

from .agent_metrics import (
    AgentMetrics,
    is_test_command
)

from .planing import (
    AgentState,
    record_progress
)


def resolve_path(path: str) -> Path:
    target = (WORKSPACE / path).resolve()

    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError("Path is outside the workspace")

    return target


def list_files(path: str) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=f"Direcotory does not exist: {path}"
        )

    if not target.is_dir():
        return ToolResult(
            success=False,
            error=f"Not a directory: {path}"
        )

    result = []
    for child in sorted(target.iterdir()):
        relative = child.relative_to(WORKSPACE)

        if child.is_dir():
            result.append(f"{relative}/")
        else:
            result.append(str(relative))

    value = "\n".join(result) or "empty directory"
    result_str = truncate_text(value)
    return ToolResult(
        success=True,
        content=result_str
    )


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=f"File does not exist: {path}"
        )

    if not target.is_file():
        return ToolResult(
            success=False,
            error=f"Not a file: {path}"
        )

    try:
        content = target.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError:
        return ToolResult(
            success=False,
            error=(
                f"Cannot read binary or "
                f"non-UTF-8 file: {path}"
            )
        )

    lines = content.splitlines()

    total_lines = len(lines)

    if total_lines == 0:
        return ToolResult(
            success=False,
            error=(
                f"File: {path}\n"
                f"(empty file)"
            )   
        )

    if start_line < 1:
        start_line = 1

    if start_line > total_lines:
        return ToolResult(
            success=False,
            error=(
                f"Start line {start_line} "
                f"is beyond the end of file.\n"
                f"Total lines: {total_lines}"
            )
        )

    if end_line is None:
        end_line = (
            start_line
            + DEFAULT_READ_LINES
            - 1
        )

    end_line = min(
        end_line,
        total_lines,
    )

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
        selected_lines,
        start=start_line,
    ):
        numbered_lines.append(
            f"{line_number:4}: {line}"
        )

    result = [
        f"File: {path}",
        (
            f"Lines {start_line}-{end_line} "
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
    value = "\n".join(result) or "empty file"
    result_str = truncate_text(value)
    return ToolResult(
        success=True,
        content=result_str
    )


def search_text(
    query: str,
    path: str = ".",
) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=(
                f"Path does not exist: {path}"
            )
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
        except (
            UnicodeDecodeError,
            PermissionError,
        ):
            continue

        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            if query.lower() in line.lower():
                relative = file.relative_to(
                    WORKSPACE
                )

                results.append(
                    (
                        f"{relative}:"
                        f"{line_number}: "
                        f"{line.strip()}"
                    )
                )

                if (
                    len(results)
                    >= MAX_SEARCH_RESULTS
                ):
                    return ToolResult(
                        success=True,
                        content=(
                            "\n".join(results)
                            + "\n\n"
                            + "[Search results truncated]"
                        )
                    )

    if not results:
        return ToolResult(
            success=False,
                error=(
                f"No matches found for: "
                f"{query}"
            )
        )

    return ToolResult(
        success=True,
        content="\n".join(results)
    )
    


def write_file(path: str, content: str) -> FileOperationResult:
    target = resolve_path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    target.write_text(
        content,
        encoding="utf-8"
    )

    # return f"Successfully wrote file: {path}"
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully wrote file: {path}"
        )
    )

def replace_text(
        path: str,
        old_text: str,
        new_text: str,
) -> FileOperationResult:
    target = resolve_path(path)

    if not target.exists():
        # return f"File does not exist: {path}"
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"File does not exist: {path}"
            )
        )

    content = target.read_text(
        encoding="utf-8"
    )

    if old_text not in content:
        # return "Old text was not found in the file"
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                "Old text was not found in the file"
            )
        )

    count = content.count(old_text)

    if count > 1:
        # return (
        #     f"Old text appears {count} times. "
        #     "Please provide more surrounding context "
        #     "so the replacement is unambiguous."
        # )
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Old text appears {count} times. "
                "Please provide more surrounding context "
                "so the replacement is unambiguous."
            )
        )
    
    updated = content.replace(
        old_text,
        new_text,
        1
    )

    if updated == content:
        return FileOperationResult(
            success=True,
            changed=False,
            message=(
                f"No changes needed: {path}"
            )
        )

    target.write_text(
        updated,
        encoding="utf-8"
    )

    # return f"Successfully updated file: {path}"
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully updated file: {path}"
        )
    )

def run_command(
        state: AgentState,
        metrics: AgentMetrics,
        command: str,
) -> CommandResult | str:

    permission = classify_command(command=command)

    if permission == Permission.BLOCKED:
        return f"Blocked dangerous command: {command}"

    if permission == Permission.CONFIRM:
        if not ask_for_confirmation(command=command):
            return "User deinied the command"

    try:
        args = shlex.split(command)
        result = subprocess.run(
            args=args,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30
        )


        if (
            is_test_command(
                command
            ) and result.returncode == 0
        ):
            record_progress(
                state=state,
                turn=metrics.model_turns
            )

        # output = ""
        # if result.stdout:
        #     output += f"STDOUT:\n{result.stdout}\n"

        # if result.stderr:
        #     output += f"STDERR:\n{result.stderr}\n"

        # output += f"EXIT CODE: {result.returncode}"
        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode
        )

    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds"

    except Exception as e:
        return f"Command error: {e}"

