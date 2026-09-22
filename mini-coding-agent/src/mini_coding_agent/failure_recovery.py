from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from .tool_result import ToolResult
import hashlib
import json
from typing import TYPE_CHECKING
from .agent_metrics import (
    is_test_command
)

if TYPE_CHECKING:
    from .planing import AgentState

class FailureKind(str, Enum):
    '''
    工具调用错误类型
    '''
    # 短暂的
    TRANSIENT = "transient"
    # 未发现
    NOT_FOUND = "not_found"
    # 无效的论据
    INVALID_ARGUMENT = "invalid_argument"
    # 权限
    PERMISSION = "permission"
    # 冲突
    CONFLICT = "conflict"
    # 测试失败
    TEST_FAILED = "test_failed"
    # 命令执行失败
    COMMAND_FAILED = "command_failed"
    # 未知的
    UNKNOWN = "unknown"

class RecoveryAction(str, Enum):
    # 重试
    RETRY = "retry"
    # 检查
    INSPECT = "inspect"
    # 变更策略
    CHANGE_STRATEGY = "change_strategy"
    # 屏蔽
    BLOCKED = "blocked"

@dataclass
class FailureDecision:
    """
    失败决策
    """
    kind: FailureKind
    action: RecoveryAction
    reason: str
    auto_retry: bool = False

SAFE_AUTO_RETRY_TOOLS = {
    "read_file",
    "list_files",
    "search_text",
}

def classify_failure(
    tool_name: str,
    arguments: dict,
    result: ToolResult,
) -> FailureKind:
    '''
    错误分类
    '''
    if result.success:
        raise ValueError(
            "classify_failure called for successful result"
        )

    error = (
        result.error
        or result.content
        or ""
    ).lower()

    # ------------------------
    # 1. 临时性错误
    # ------------------------

    transient_keywords = [
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "resource temporarily unavailable",
    ]

    if any(
        keyword in error
        for keyword in transient_keywords
    ):
        return FailureKind.TRANSIENT

    # ------------------------
    # 2. 权限错误
    # ------------------------

    permission_keywords = [
        "permission denied",
        "operation not permitted",
        "access denied",
        "blocked dangerous command",
        "user denied",
    ]

    if any(
        keyword in error
        for keyword in permission_keywords
    ):
        return FailureKind.PERMISSION

    # ------------------------
    # 3. 文件 / 路径不存在
    # ------------------------

    not_found_keywords = [
        "file not found",
        "directory not found",
        "file does not exist",
        "directory does not exist",
        "no such file",
        "path does not exist",
    ]

    if any(
        keyword in error
        for keyword in not_found_keywords
    ):
        return FailureKind.NOT_FOUND

    # ------------------------
    # 4. replace_text 冲突
    # ------------------------

    if tool_name == "replace_text":

        conflict_keywords = [
            "old text not found",
            "old text was not found",
            "target text not found",
            "no matching text",
        ]

        if any(
            keyword in error
            for keyword in conflict_keywords
        ):
            return FailureKind.CONFLICT

    # ------------------------
    # 5. 参数错误
    # ------------------------

    invalid_argument_keywords = [
        "invalid argument",
        "missing argument",
        "invalid path",
        "invalid range",
    ]

    if any(
        keyword in error
        for keyword in invalid_argument_keywords
    ):
        return FailureKind.INVALID_ARGUMENT

    # ------------------------
    # 6. run_command
    # ------------------------

    if tool_name == "run_command":

        command = arguments.get(
            "command",
            ""
        )

        if is_test_command(command):
            return FailureKind.TEST_FAILED

        return FailureKind.COMMAND_FAILED

    return FailureKind.UNKNOWN


def decide_recovery(
    tool_name: str,
    failure_kind: FailureKind,
    failure_count: int,
) -> FailureDecision:

    '''
    决定怎样恢复
    '''

    # ========================
    # 连续失败太多
    # ========================

    if failure_count >= 3:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.BLOCKED,
            reason=(
                "The same tool failure has occurred "
                "repeatedly. Do not repeat the same "
                "operation again."
            ),
            auto_retry=False,
        )

    # ========================
    # 临时错误
    # ========================

    if failure_kind == FailureKind.TRANSIENT:

        if (
            tool_name
            in SAFE_AUTO_RETRY_TOOLS
            and failure_count == 1
        ):
            return FailureDecision(
                kind=failure_kind,
                action=RecoveryAction.RETRY,
                reason=(
                    "The failure appears transient "
                    "and the tool is safe to retry."
                ),
                auto_retry=True,
            )

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.CHANGE_STRATEGY,
            reason=(
                "Retry is not safe or the transient "
                "error has already repeated."
            ),
        )

    # ========================
    # 文件不存在
    # ========================

    if failure_kind == FailureKind.NOT_FOUND:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.INSPECT,
            reason=(
                "The requested path does not exist. "
                "Inspect the workspace and find the "
                "correct path before trying again."
            ),
        )

    # ========================
    # replace_text 冲突
    # ========================

    if failure_kind == FailureKind.CONFLICT:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.INSPECT,
            reason=(
                "The expected text no longer matches "
                "the file. Re-read the relevant section "
                "before editing again."
            ),
        )

    # ========================
    # 参数错误
    # ========================

    if failure_kind == FailureKind.INVALID_ARGUMENT:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.CHANGE_STRATEGY,
            reason=(
                "The tool arguments are invalid. "
                "Correct the arguments instead of "
                "repeating the same call."
            ),
        )

    # ========================
    # 权限错误
    # ========================

    if failure_kind == FailureKind.PERMISSION:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.BLOCKED,
            reason=(
                "The operation is not permitted "
                "under the current tool permissions."
            ),
        )

    # ========================
    # 测试失败
    # ========================

    if failure_kind == FailureKind.TEST_FAILED:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.CHANGE_STRATEGY,
            reason=(
                "Tests failed. Inspect the failure "
                "output, fix the code, and then run "
                "the tests again."
            ),
        )

    # ========================
    # 普通命令失败
    # ========================

    if failure_kind == FailureKind.COMMAND_FAILED:

        return FailureDecision(
            kind=failure_kind,
            action=RecoveryAction.CHANGE_STRATEGY,
            reason=(
                "The command failed. Inspect its "
                "output and choose the next action "
                "based on the error."
            ),
        )

    # ========================
    # Unknown
    # ========================

    return FailureDecision(
        kind=FailureKind.UNKNOWN,
        action=RecoveryAction.CHANGE_STRATEGY,
        reason=(
            "The tool failed for an unknown reason. "
            "Inspect the error before deciding what "
            "to do next."
        ),
    )


def make_failure_signature(
    tool_name: str,
    arguments: dict,
    failure_kind: FailureKind,
) -> str:
    '''
    构建错误签名
    讲错误进行hash
    结果应该是这样：read_file:not_found:a87f153922ab
    '''
    arguments_json = json.dumps(
        arguments,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )

    arguments_hash = hashlib.sha1(
        arguments_json.encode("utf-8")
    ).hexdigest()[:12]

    return (
        f"{tool_name}:"
        f"{failure_kind.value}:"
        f"{arguments_hash}"
    )


def record_failure(
        state: AgentState,
        signature: str,
) -> int:
    '''
    记录错误并返回错误个数
    如果是同一个错误连续出现，consecutive_count就会增加
    '''

    failure_state = state.failure
    if (failure_state.last_signature == signature):
        failure_state.consecutive_count += 1

    else:
        failure_state.last_signature = signature
        failure_state.consecutive_count = 1

    return failure_state.consecutive_count


def clear_failure_streak(
        state: AgentState
) -> None:
    state.failure.last_signature = None
    state.failure.consecutive_count = 0


def handle_tool_failure(
    tool_name: str,
    arguments: dict,
    result: ToolResult,
    state,
) -> tuple[
    FailureDecision,
    int,
]:
    '''
    处理出现错误
    '''
    # 对错误进行分类
    failure_kind = classify_failure(
        tool_name=tool_name,
        arguments=arguments,
        result=result,
    )

    # 对错误签名
    signature = make_failure_signature(
        tool_name=tool_name,
        arguments=arguments,
        failure_kind=failure_kind,
    )

    # 记录错误到state
    failure_count = record_failure(
        state=state,
        signature=signature,
    )

    # 决定怎样恢复
    decision = decide_recovery(
        tool_name=tool_name,
        failure_kind=failure_kind,
        failure_count=failure_count,
    )

    return (
        decision,
        failure_count,
    )


def build_failure_result(
    original_result: ToolResult,
    decision: FailureDecision,
    failure_count: int,
) -> ToolResult:
    '''
    构建失败的原因
    '''
    message = (
        f"{original_result.error}\n\n"
        f"RUNTIME RECOVERY:\n"
        f"- failure_kind: "
        f"{decision.kind.value}\n"
        f"- recovery_action: "
        f"{decision.action.value}\n"
        f"- repeated_count: "
        f"{failure_count}\n"
        f"- instruction: "
        f"{decision.reason}"
    )

    return ToolResult.fail(
        error=message,
        content=original_result.content,
        return_code=(
            original_result.return_code
        ),
    )
