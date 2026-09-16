"""Command execution, user confirmation and result formatting."""

import shlex
import subprocess
from ..config import WORKSPACE
from ..context import truncate_tail
from ..permission import (
    Permission,
    classify_command,
)
from ..task_state import CommandResult

# 询问是不是要继续
def ask_for_confirmation(command: str) -> bool:
    print("\n⚠️ Agent wants to run:")
    print(command)

    answer = input(
        "\nDo you want to allow this command? [y/N]: "
    )

    answer = answer.strip().lower()

    print(f"DEBUG answer: {answer!r}")

    return answer in {"y", "yes"}


# 运行命令
def run_command(command: str) -> CommandResult | str:

    permission = classify_command(command)
    if permission == Permission.BLOCKED:
        return f"Command is blocked for safety reasons: {command}"

    if permission == Permission.CONFIRM:
        if not ask_for_confirmation(command):
            return "User denied the command."
    
    try:
        args = shlex.split(command)

        result = subprocess.run(
            args,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # output = ""
        # if result.stdout:
        #     output += f"STDOUT:\n{result.stdout.strip()}\n"
        # if result.stderr:
        #     output += f"STDERR:\n{result.stderr.strip()}\n"

        # output += f"Exit code: {result.returncode}"
        # return output.strip()
        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except subprocess.CalledProcessError as e:
        return f"Command failed with exit code {e.returncode}:\n{e.stderr.strip()}"
    except Exception as e:
        return f"Command error: {e}"


def format_command_result(
        result: CommandResult
) -> str:
    parts = []
    if result.stdout:
        stdout = truncate_tail(
            result.stdout.strip()
        )
        parts.append(
            f"STDOUT:\n{stdout}\n"
        )
    if result.stderr:
        stderr = truncate_tail(
            result.stderr.strip()
        )
        parts.append(
            f"STDERR:\n{stderr}\n"
        )
    parts.append(
        f"EXIT CODE: {result.returncode}"
    )

    return "\n".join(parts)
