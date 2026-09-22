'''验证'''

from dataclasses import dataclass
from enum import Enum
from .planing import (
    AgentState,
)
from .tool_result import ToolResult
from .agent_metrics import is_test_command

class VerificationKind(str, Enum):
    '''
    需要验证的类型
    '''
    # 查看修改内容
    DIFF = "diff"
    # test
    TEST = "test"
    # build
    BUILD = "build"
    # py_compile 等轻量验证
    SYNTAX = "syntax"
    # 修改后重新读取文件确认
    READBACK = "readback"

@dataclass
class VerificationRecord:
    '''
    验证记录
    '''
    kind: VerificationKind
    success: bool
    workspace_revision: int
    code_revision: int
    source: str


CODE_EXTENSIONS = {
    ".py",
    ".go",
    ".swift",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".kt",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
}


def is_code_file(
    path: str,
) -> bool:

    from pathlib import Path

    suffix = (
        Path(path)
        .suffix
        .lower()
    )

    return (
        suffix
        in CODE_EXTENSIONS
    )


def record_verification(
        state: AgentState,
        kind: VerificationRecord,
        success: bool,
        source: str,
        detail: str = ""
) -> None:
    record = VerificationRecord(
        kind=kind,
        success=success,
        workspace_revision=state.workspace_revision,
        code_revision=state.code_revision,
        source=source,
        detail=detail
    )

    state.verification.records.append(
        record
    )


def is_diff_command(
        command: str,
) -> bool:

    command = (
        command.strip().lower()
    )

    return command.startswith("git diff")


def is_build_command(
        command: str,
) -> bool:
    command = command.lower()

    keywords = [
        "go build",
        "swift build",
        "npm run build",
        "pnpm build",
        "yarn build",
        "xcodebuild",
        "cargo build",
    ]

    return any(
        keyword in command
        for keyword in keywords
    )


def is_syntax_command(
    command: str,
) -> bool:

    command = command.lower()

    keywords = [
        "py_compile",
        "compileall",
    ]

    return any(
        keyword in command
        for keyword in keywords
    )


def record_command_verification(
    state,
    command: str,
    result: ToolResult,
) -> None:

    success = (
        result.return_code == 0
    )

    detail = (
        result.content[-1000:]
        if result.content
        else result.error[-1000:]
    )

    if is_test_command(command):

        record_verification(
            state=state,
            kind=VerificationKind.TEST,
            success=success,
            source=command,
            detail=detail,
        )

        return

    if is_diff_command(command):

        record_verification(
            state=state,
            kind=VerificationKind.DIFF,
            success=success,
            source=command,
            detail=detail,
        )

        return

    if is_build_command(command):

        record_verification(
            state=state,
            kind=VerificationKind.BUILD,
            success=success,
            source=command,
            detail=detail,
        )

        return

    if is_syntax_command(command):

        record_verification(
            state=state,
            kind=VerificationKind.SYNTAX,
            success=success,
            source=command,
            detail=detail,
        )


def has_successful_code_verification(
    state,
) -> bool:

    for record in reversed(
        state.verification.records
    ):

        if (
            record.code_revision
            != state.code_revision
        ):
            continue

        if (
            record.kind
            in {
                VerificationKind.TEST,
                VerificationKind.BUILD,
                VerificationKind.SYNTAX,
            }
            and record.success
        ):
            return True

    return False