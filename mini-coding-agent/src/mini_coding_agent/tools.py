"""Tool implementations: filesystem reads/writes and shell execution."""

import shlex
import subprocess
from pathlib import Path
from .tool_result import ToolResult

# 说明：is_code_file（定义于 verification.py，用于按扩展名判断文件是否为代码文件）
# 在本模块的文件写入类工具（write_file / replace_text）的调用链中被使用：
# tool_runner 会先通过 is_code_file(path) 判断目标路径是否为代码文件，
# 再决定是否触发相应的代码验证流程。此处仅添加注释说明该关联，
# 本模块自身不直接定义或导入 is_code_file。

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

from .planning import (
    AgentState,
    # record_progress
)

PROTECTED_COMPONENTS = {".env", ".git", ".venv", ".mini_agent"}


def resolve_path(path: str) -> Path:
    """Keep file tools inside the workspace and away from private internals.

    This does not sandbox `run_command`, which requires a separate OS-level
    security boundary if project content is untrusted.
    """
    root = WORKSPACE.resolve()
    target = (root / path).resolve()

    if target != root and root not in target.parents:
        raise ValueError("Path is outside the workspace")

    relative = target.relative_to(root)
    # 增加一层敏感文件保护
    if any(part in PROTECTED_COMPONENTS for part in relative.parts):
        raise ValueError("Protected workspace path is not accessible")

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
    


MAX_ASK_USER_ATTEMPTS = 3


def clean_ask_user_options(
        options: list[str],
) -> list[str]:
    """Normalize option strings and drop empty entries."""
    return [
        str(option).strip()
        for option in options
        if str(option).strip()
    ]


def prompt_user_choice(
        question: str,
        options: list[str],
) -> tuple[str | None, str]:
    """Render a numbered question and read one answer.

    Returns ``(selected_option, status)`` where status is one of:

    - ``"selected"``: ``selected_option`` is the chosen option text;
    - ``"zero"``: the user answered 0 / pressed Enter;
    - ``"closed"``: input was closed (EOF / Ctrl-C);
    - ``"exhausted"``: too many invalid answers.
    """
    print("\n🤔 Agent asks:")
    print(question)
    print()

    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")

    print("  0. None of the above / no answer")

    for _ in range(MAX_ASK_USER_ATTEMPTS):
        try:
            answer = input(
                f"\nYour answer [0-{len(options)}]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None, "closed"

        # 直接回车视为“没有答案”
        if answer == "":
            answer = "0"

        if answer == "0":
            return None, "zero"

        if answer.isdigit():
            choice = int(answer)

            if 1 <= choice <= len(options):
                return options[choice - 1], "selected"

        print(
            "Invalid answer. Enter a number between "
            f"0 and {len(options)}."
        )

    return None, "exhausted"


def ask_user_choice(
        question: str,
        options: list[str],
) -> str | None:
    """Interactively pick one option and return its text.

    Returns ``None`` when the user answers 0, input is closed, or no
    valid answer is given. This helper is also used by internal
    runtime flows that need to disambiguate a choice without going
    through a model tool call.
    """
    cleaned_options = clean_ask_user_options(options)

    if not cleaned_options:
        return None

    selected, _ = prompt_user_choice(
        question=question,
        options=cleaned_options,
    )

    return selected


def ask_user(
        question: str,
        options: list[str],
) -> ToolResult:
    """Ask the user to choose one of the given options.

    Options are presented with 1-based numbers. The user may answer 0
    to indicate that none of the options apply or that they have no
    answer. The response is returned to the model as tool content.
    """
    cleaned_options = clean_ask_user_options(options)

    if not cleaned_options:
        return ToolResult(
            success=False,
            error=(
                "ask_user requires at least one "
                "non-empty option."
            )
        )

    selected, status = prompt_user_choice(
        question=question,
        options=cleaned_options,
    )

    if status == "selected":
        choice = cleaned_options.index(selected) + 1

        return ToolResult.ok(
            content=(
                f"User selected option {choice}: "
                f"{selected}"
            )
        )

    if status == "closed":
        return ToolResult.ok(
            content=(
                "User did not answer "
                "(input was closed). "
                "Treat this as answer 0 and proceed "
                "using your best judgment."
            )
        )

    if status == "exhausted":
        return ToolResult.ok(
            content=(
                "User did not give a valid answer after "
                f"{MAX_ASK_USER_ATTEMPTS} attempts. "
                "Treat this as answer 0 (no option selected). "
                "Proceed using your best judgment or mark "
                "the work as blocked with a reason."
            )
        )

    return ToolResult.ok(
        content=(
            "User selected 0: none of the options apply. "
            "The user has no answer or asked you to "
            "proceed without choosing an option."
        )
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


        # if (
        #     is_test_command(
        #         command
        #     ) and result.returncode == 0
        # ):
        #     record_progress(
        #         state=state,
        #         turn=metrics.model_turns
            # )

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

def delete_file(path: str) -> FileOperationResult:
    target = resolve_path(path)

    if not target.exists():
        return FileOperationResult(
            success=True,
            changed=False,
            message=f"Already absent: {path}",
        )

    if not target.is_file() or target.is_symlink():
        return FileOperationResult(
            success=False,
            changed=False,
            message="Target must be a regular file",
        )

    target.unlink()

    return FileOperationResult(
        success=True,
        changed=True,
        message=f"Deleted file: {path}",
    )