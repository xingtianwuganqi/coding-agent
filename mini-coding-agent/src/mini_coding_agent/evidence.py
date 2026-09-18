from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .planing import AgentState


class EvidenceType(Enum):
    """
    后面可加入
    BUILD_PASSED
    LINT_PASSED
    TYPE_CHECK_PASSED
    FILE_READ
    USER_APPROVED
    DEPLOY_SUCCEEDED
    """
    # 测试真实运行，并且退出码为 0
    TESTS_PASSED = "tests_passed"
    # git diff 真实执行成功
    DIFF_INSPECTED = "diff_inspected"

@dataclass
class CommandResult:
    """
    命令执行结果
    """
    command: str
    stdout: str
    stderr: str
    returncode: int

@dataclass
class FileOperationResult:
    """
    文件是否修改
    """
    success: bool
    message: str
    changed: bool = False


def format_command_result(
        result: CommandResult,
) -> str:
    """
    将执行命令的结果格式化，再给llm
    """
    # Import after modules initialize to avoid the planning/context cycle.
    from .context import truncate_tail

    output = ""

    if result.stdout:
        # 截取后半部分的内容
        stdout = truncate_tail(
            result.stdout
        )
        output += (
            f"STDOUT:\n"
            f"{stdout}\n"
        )

    if result.stderr:
        # 对返回的内容进行截取
        stderr = truncate_tail(
            result.stderr
        )
        output += (
            f"STDERR:\n"
            f"{stderr}\n"
        )

    output += (
        f"EXIT CODE:"
        f"{result.returncode}"
    )

    return output


def record_command_evidence(
    state: AgentState,
    result: CommandResult,
) -> None:
    try:
        args = shlex.split(result.command)
    except ValueError:
        return

    if not args:
        return

    evidence_type = None

    if args[0] == "pytest" or args[:3] == ["uv", "run", "pytest"]:
        evidence_type = EvidenceType.TESTS_PASSED
    elif args[:2] == ["git", "diff"]:
        evidence_type = EvidenceType.DIFF_INSPECTED

    if evidence_type is None:
        return

    if result.returncode == 0:
        state.evidence[evidence_type] = state.workspace_revision
    else:
        state.evidence.pop(evidence_type, None)


def mark_workspace_changed(
        state: AgentState
) -> None:
    state.workspace_revision += 1

# 写一个判断Evidence是否有效的方法
def has_valid_evidence(
        state: AgentState,
        evidence_type: EvidenceType
) -> bool:

    evidence_revision = state.evidence.get(
        evidence_type
    )

    if evidence_revision is None:
        return False

    return evidence_revision == state.workspace_revision

# Evidence Invalidation / Dirty State（证据失效 / 脏状态）

# def invalidate_evidence(
#         state: AgentState,
# ) -> None:
#     """
#     清空state中的evidence
#     """
#     state.evidence.discard(
#         EvidenceType.TESTS_PASSED
#     )

#     state.evidence.discard(
#         EvidenceType.DIFF_INSPECTED
#     )