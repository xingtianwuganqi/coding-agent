'''任务需求提取'''

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from .tool_result import ToolResult

if TYPE_CHECKING:
    from .planing import AgentState

class RequirementKind(str, Enum):
    # 用户明确要求某个文件/模块必须修改
    MUST_CHANGE = 'must_change'
    # 用户明确禁止修改某个文件/目录
    MUST_NOT_MODIFY = 'must_not_modify'
    # 用户明确要求某个验证
    MUST_VERIFY = 'must_verify'
    # runtime 暂时无法机械证明的需求
    SOFT_CONSTRAINT = 'soft_constraint'


@dataclass
class TaskRequirement:
    id: int
    kind: RequirementKind
    description: str
    target: str | None = None
    verifier: str | None = None
    command_contains: str | None = None

@dataclass
class RequirementState:
    items: list[TaskRequirement] = field(
        default_factory=list
    )

    locked: bool = False

@dataclass 
class RequirementCheck:
    '''
    requirement 检查
    '''
    requirement_id: int
    satisfied: bool
    reason: str

def set_requirements(
    state: AgentState,
    requirements: list[dict],
) -> ToolResult:
    '''
    构建需求
    '''
    # 已经锁定
    if state.requirements.locked:
        return ToolResult.fail(
            error=(
                "Requirements are already locked "
                "and cannot be replaced."
            )
        ) 

    parsed: list[TaskRequirement] = []

    for index, item in enumerate(requirements):
        kind_value = item.get('kind')

        description = item.get("description", "")

        target = item.get('target')
        verifier = item.get('verifier')
        command_contains = item.get('command_contains')

        try:
            kind = RequirementKind(
                kind_value
            )

        except ValueError:
            return ToolResult.fail(
                error=(
                    "Invalid requirement kind: "
                    f"{kind_value}"
                )
            )

        if not description:
            return ToolResult.fail(
                error=(
                    "Requirement description "
                    "cannot be empty"
                )
            )

        if kind in {
            RequirementKind.MUST_CHANGE,
            RequirementKind.MUST_NOT_MODIFY
        }:
            # MUST_CHANGE 可以target=None
            # 表示“必须发生文件修改”
            if (
                kind == RequirementKind.MUST_NOT_MODIFY
                and not target
            ):
                return ToolResult.fail(
                    error=(
                        "must_not_modify requires "
                        "a target path."
                    )
                )

        if kind == RequirementKind.MUST_VERIFY:
            allowed_verifiers = {
                'test',
                'build',
                'syntax',
                'diff'
            }

            if verifier not in allowed_verifiers:
                return ToolResult.fail(
                    error=(
                        "must_verifier requires "
                        "verifier to be one of: "
                        "test, build, syntax, diff"
                    )
                )

        parsed.append(
            TaskRequirement(
                id=index+1,
                kind=kind,
                description=description,
                target=target,
                verifier=verifier,
                command_contains=command_contains
            )
        )

    # 保存并锁定
    state.requirements.items = parsed
    state.requirements.locked = True
    return ToolResult.ok(
        content=(
            f"Recorded "
            f"{len(parsed)} requirements."
        )
    )


def build_requirement_context(
        state: AgentState,
) -> str:

    if not state.requirements.locked:
        return (
            "Task requirements have not "
            "been extracted yet"
        )

    lines = [
        "TASK REQUIREMENTS:",
    ]

    for requirement in (
        state.requirements.items
    ):

        line = (
            f"{requirement.id}. "
            f"[{requirement.kind.value}] "
            f"{requirement.description}"
        )

        if requirement.target:

            line += (
                f" | target="
                f"{requirement.target}"
            )

        if requirement.verifier:

            line += (
                f" | verifier="
                f"{requirement.verifier}"
            )

        if (
            requirement
            .command_contains
        ):

            line += (
                " | command_contains="
                f"{requirement.command_contains}"
            )

        lines.append(line)

    return "\n".join(lines)


from pathlib import PurePosixPath


def normalize_path(
    path: str,
) -> str:
    return (
        str(
            PurePosixPath(path)
        ).replace("\\", "/")
        .lstrip("./")
    )

def path_matches_target(
        path: str,
        target: str,
) -> bool:

    normalized_path = normalize_path(path)

    normalized_target = normalize_path(target)

    path_obj = PurePosixPath(
        normalized_path
    )

    target_obj = PurePosixPath(
        normalized_target
    )

    if path_obj == target_obj:
        return True

    return target_obj in path_obj.parents


MUTATION_TOOLS = {
    "write_file",
    "replace_text",
    "delete_file",
    "rename_file",
}

def guard_requirement_constraints(
        name: str,
        arguments: dict,
        state: AgentState
) -> str | None:

    '''
    守卫 需求中的包含的路径
    '''

    if name not in MUTATION_TOOLS:
        return None

    path = arguments.get(
        "path"
    )

    if not path:
        return None

    for requirement in state.requirements.items:
        if requirement.kind != RequirementKind.MUST_NOT_MODIFY:
            continue

        if not requirement.target:
            continue

        if path_matches_target(
            path=path,
            target=requirement.target
        ):
            return (
                "RUNTIME REQUIREMENT GUARD: "
                f"modifying '{path}' would violate "
                f"requirement {requirement.id}: "
                f"{requirement.description}"
            )

    return None


def has_required_verification(
    state,
    requirement: TaskRequirement,
) -> bool:

    

    expected_kind = (
        requirement.verifier
    )

    for record in reversed(
        state.verification.records
    ):

        if not record.success:
            continue

        # =====================
        # Verification 类型
        # =====================

        if (
            record.kind.value
            != expected_kind
        ):
            continue

        # =====================
        # 版本检查
        # =====================

        if (
            expected_kind
            == "diff"
        ):

            if (
                record.workspace_revision
                != state.workspace_revision
            ):
                continue

        else:

            if (
                record.code_revision
                != state.code_revision
            ):
                continue

        # =====================
        # 用户指定具体命令
        # =====================

        if (
            requirement.command_contains
        ):

            command_part = (
                requirement
                .command_contains
                .lower()
            )

            source = (
                record.source
                .lower()
            )

            if (
                command_part
                not in source
            ):
                continue

        return True

    return False


def evaluate_requirement(
        state: AgentState,
        requirement: TaskRequirement,
) -> RequirementCheck:

    # MUST_CHANGE
    if requirement.kind == RequirementKind.MUST_CHANGE:
        # 没指定 target
        # 只要求至少发生一次 Workspace Change
        if not requirement.target:
            satisfied = bool(state.changed_files)

            return RequirementCheck(
                requirement_id=requirement.id,
                satisfied=satisfied,
                reason="Workspace change required."
            )

        satisfied = any(
            path_matches_target(
                path=changed_path,
                target=requirement.target,
            )
            for changed_path in state.changed_files
        )

        return RequirementCheck(
            requirement_id=requirement.id,
            satisfied=satisfied,
            reason=(
                    f"Required target "
                    f"'{requirement.target}' "
                    f"must be changed"
            )
        )

    # MUST_NOT_MODIFY

    if requirement.kind == RequirementKind.MUST_NOT_MODIFY:
        violated = any(
            path_matches_target(
                path=changed_path,
                target=requirement.target
            )
            for changed_path in state.changed_files
        )

        return RequirementCheck(
            requirement_id=requirement.id,
            satisfied=not violated,
            reason=(
                f"Forbidden target "
                f"'{requirement.target}' "
                f"must remain unchanged"
            )
        )

    # MUST_VERIFY

    if requirement.kind == RequirementKind.MUST_VERIFY:
        satisfied = has_required_verification(
            state=state,
            requirement=requirement,
        )

        return RequirementCheck(
            requirement_id=requirement.id,
            satisfied=satisfied,
            reason=(
                "Required verification "
                "has not passed for the "
                "current revision."
            )
        )

    # SOFT_CONSTRAINT

    return RequirementCheck(
        requirement_id=requirement.id,
        satisfied=True,
        reason=(
            "Soft constraint is preserved"
            "in runtime context but is not "
            "mechanically verified"
        )
    )
