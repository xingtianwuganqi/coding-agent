"""工具权限功能，不能让ai 全权限的执行命令"""

from enum import Enum
import shlex

# 权限
class Permission(Enum):
    SAFE = "safe"
    WRITE = "write"
    CONFIRM = "confirm"
    BLOCKED = "blocked"

# 工具权限
TOOL_PERMISSIONS = {
    'list_files': Permission.SAFE,
    'read_file': Permission.SAFE,
    "write_file": Permission.WRITE,
    "replace_text": Permission.WRITE,
    "run_command": Permission.SAFE,
    "set_plan": Permission.SAFE,
    "get_plan": Permission.SAFE,
    "update_task": Permission.SAFE,
    "search_text": Permission.SAFE,
}

# 被禁止的shell语法
SHELL_OPERATORS = [
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "`",
    "$(",
]


def contains_shell_operator(command: str) -> bool:
    """
    判断命令是否包含被禁止的shell命令
    """
    return any(
        operator in command
        for operator in SHELL_OPERATORS
    )

def classify_command(command: str) -> Permission:
    """
    权限判断
    """
    if contains_shell_operator(command=command):
        return Permission.BLOCKED

    try:
        args = shlex.split(command)
    except ValueError:
        return Permission.BLOCKED

    if not args:
        return Permission.BLOCKED

    program = args[0]
    if program == "git":
        return classify_git_command(args=args)

    if program == "uv":
        return classify_uv_command(args=args)

    if program == "pwd":
        return Permission.SAFE

    if program == "ls":
        return Permission.SAFE

    if program == "pytest":
        return Permission.SAFE

    return Permission.BLOCKED

def classify_git_command(
        args: list[str]
) -> Permission:
    if len(args) < 2:
        return Permission.BLOCKED

    subcommand = args[1]

    safe_commands = {
        "status",
        "diff",
        "log",
        "show",
        "branch",
    }

    confirm_commands = {
        "add",
        "commit",
        "checkout",
        "switch",
        "restore",
        "reset",
        "merge",
        "rebase",
        "push",
        "pull",
    }

    if subcommand in safe_commands:
        return Permission.SAFE

    if subcommand in confirm_commands:
        return Permission.CONFIRM

    return Permission.BLOCKED


def classify_uv_command(
        args: list[str]
) -> Permission:
    if len(args) < 2:
        return Permission.BLOCKED

    subcommand = args[1]

    if subcommand == "add":
        return Permission.CONFIRM

    if subcommand == "remove":
        return Permission.CONFIRM

    if subcommand == "sync":
        return Permission.CONFIRM

    if subcommand == "run":
        if len(args) < 3:
            return Permission.BLOCKED

        target = args[2]

        if target in {
            "pytest",
        }:
            return Permission.SAFE

        return Permission.CONFIRM

    return Permission.BLOCKED

def ask_for_confirmation(command: str) -> bool:
    print("\n⚠️ Agent wants to run:")
    print(command)

    answer = input(
        "\nDo you want to allow this command? [y/N]: "
    )

    answer = answer.strip().lower()

    print(f"DEBUG answer: {answer!r}")

    return answer in {"y", "yes"}