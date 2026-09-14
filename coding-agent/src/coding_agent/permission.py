from enum import Enum
import shlex

class Permission(Enum):
    SAFE = "safe"
    WRITE = "write"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


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
    Check if the command contains shell operators that could be dangerous.
    """
    return any(op in command for op in SHELL_OPERATORS)

def classify_git_command(args: list[str]) -> Permission:
    """
    Classify git commands based on their potential impact.
    """
    if len(args) < 2:
        return Permission.BLOCKED
    
    subcommand = args[1]
    safe_subcommands = {
        "status", 
        "log",
        "diff", 
        "show", 
        "branch", 
    }

    confirm_subcommands = {
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
    if subcommand in safe_subcommands:
        return Permission.SAFE
    if subcommand in confirm_subcommands:
        return Permission.CONFIRM
    return Permission.BLOCKED

def classify_uv_command(args: list[str]) -> Permission:
    """
    Classify uv commands based on their potential impact.
    """
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


def classify_command(command: str) -> Permission:
    """
    Classify the command based on its safety level.
    """

    if contains_shell_operator(command):
        return Permission.BLOCKED

    try:
        args = shlex.split(command)
    except ValueError:
        return Permission.BLOCKED

    program = args[0]

    if program == "git":
        return classify_git_command(args)

    if program == "uv":
        return classify_uv_command(args)

    if program == "pwd":
        return Permission.SAFE

    if program == "ls":
        return Permission.SAFE

    if program == "pytest":
        return Permission.SAFE

    return Permission.BLOCKED