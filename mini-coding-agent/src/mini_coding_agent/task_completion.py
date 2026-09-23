from .planing import (
    AgentState,
    TaskStatus,
    TodoItem,
    has_valid_evidence,
    format_plan
)
from enum import Enum
from dataclasses import dataclass
from .verification import (
    has_current_diff_verification,
    has_successful_code_verification
)

from .requirements import (
    RequirementKind,
    evaluate_requirement
)

class CompletionStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"



@dataclass
class CompletionRequirement:
    name: str
    # 使满意
    satisfied: bool
    reason: str


@dataclass
class CompletionReport:
    complete: CompletionStatus

    requirements: list[CompletionRequirement]

    @property
    def missing(
        self
    ) -> list[CompletionRequirement]:
        return [
            requirement
            for requirement
            in self.requirements
            if not requirement.satisfied
        ]

def get_unfinished_tasks(
        state: AgentState
) -> list[TodoItem]:
    """
    获取未完成的任务
    """
    for todo in state.todos:
        if (todo.status == TaskStatus.COMPLETED
                and todo.required_evidence is not None
                and not has_valid_evidence(
                    state=state, 
                    evidence_type=todo.required_evidence)
        ):
            todo.status = TaskStatus.PENDING
    return [
        todo 
        for todo in state.todos
        if todo.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED
        }
    ]

def has_blocked_tasks(
        state: AgentState,
) -> bool:
    """
    是否有存在一个被禁止
    """
    return any(
        todo.status == TaskStatus.BLOCKED
        for todo in state.todos
    )

def is_task_complete(
        state: AgentState
) -> bool:
    """
    是否所有的任务都完成了
    """
    get_unfinished_tasks(state)
    if not state.todos:
        return True

    return all(
        todo.status == TaskStatus.COMPLETED
        for todo in state.todos
    )

def get_completion_status(
        state: AgentState,
) -> CompletionStatus:
    """
    获取完成的状态
    """

    if not state.todos:
        return CompletionStatus.INCOMPLETE

    unfinished = get_unfinished_tasks(state)

    if unfinished:
        return CompletionStatus.INCOMPLETE

    if has_blocked_tasks(state):
        return CompletionStatus.BLOCKED

    return CompletionStatus.COMPLETE


def evaluate_completion(
        state: AgentState
) -> CompletionReport:

    requirements = []

    # 1.必须有plan
    has_plan = bool(state.todos)

    requirements.append(
        CompletionRequirement(
            name="plan_exists",
            satisfied=has_plan,
            reason="A task plan must exist"
        )
    )

    # # 2.必须全部完成
    # all_completed = (
    #     bool(state.todos)
    #     and all(
    #         todo.status == TaskStatus.COMPLETED
    #         for todo in state.todos
    #     )
    # )

    # requirements.append(
    #     CompletionRequirement(
    #         name="todos_completed",
    #         satisfied=all_completed,
    #         reason="All todo items must be completed"
    #     )
    # )

    # # 3.不能有Blocked Task
    # has_blocked = any(
    #     todo.status == TaskStatus.BLOCKED
    #     for todo in state.todos
    # )

    # requirements.append(
    #     CompletionRequirement(
    #         name="no_blocked_tasks",
    #         satisfied=not has_blocked,
    #         reason="Blocked tasks remain unresolved"
    #     )
    # )

    # 如果修改文件，必须检查diff
    if state.changed_files:
        diff_verified = (
            has_current_diff_verification(
                state=state
            )
        )

        requirements.append(
            CompletionRequirement(
                name="diff_verified",
                satisfied=diff_verified,
                reason=(
                    "Workspace changed but the "
                    "current revision has not been "
                    "reviewed with git diff."
                )
            )
        )

    # 如果修改代码，必须验证代码
    code_changed = (
        state.code_revision > 0
    )

    if code_changed:
        code_verified = (
            has_successful_code_verification(
                state=state
            )
        )

        requirements.append(
            CompletionRequirement(
                name="code_verified",
                satisfied=code_verified,
                reason=(
                    "Code changed but there is no "
                    "successful test/build/syntax "
                    "verification for the current "
                    "code revision."
                )
            )
        )

    if not state.requirements.locked:
        requirements.append(
            CompletionRequirement(
                name="requirements_defined",
                satisfied=False,
                reason=(
                    "Task requirements have "
                    "not been extracted"
                )
            )
        )
    else:
        for task_requirement in state.requirements.items:

            # soft constraints 暂时不参与 hard completion
            if task_requirement.kind == RequirementKind.SOFT_CONSTRAINT:
                continue

            check = evaluate_requirement(
                state=state,
                requirement=task_requirement
            )

            requirements.append(
                CompletionRequirement(
                    name=(
                        "user_requirement_"
                        f"{task_requirement.id}"
                    ),
                    satisfied=check.satisfied,
                    reason=check.reason
                )
            )

    other_complete = all(
        requirement.satisfied
        for requirement in requirements
    )

    # 有未完成的任务
    status = get_completion_status(
        state=state
    )
    comp_status: CompletionStatus = CompletionStatus.INCOMPLETE
    if other_complete and status == CompletionStatus.COMPLETE:
        comp_status = CompletionStatus.COMPLETE
        requirements.append(
            CompletionRequirement(
                name="todos_completed",
                satisfied=True,
                reason="All todo items has completed"
            )
        )
    elif status == CompletionStatus.BLOCKED and state.requirements.locked:
        comp_status = CompletionStatus.BLOCKED
        requirements.append(
            CompletionRequirement(
                name="todos_contains_blocked",
                satisfied=True,
                reason="todo items contains blocked item"
            )
        )
    else:
        comp_status = CompletionStatus.INCOMPLETE
        requirements.append(
            CompletionRequirement(
                name="todos_incomplete",
                satisfied=False,
                reason="todos incomplete"
            )
        )



    return CompletionReport(
        complete=comp_status,
        requirements=requirements,
    )


def build_completion_feedback(
        report: CompletionReport,
        state: AgentState
) -> str:

    '''
    构建是否完成的反馈
    '''
    if report.complete == CompletionStatus.COMPLETE:
        return (
            "All completion requirements "
            "are satisfied."
        )

    if report.complete == CompletionStatus.BLOCKED:
        missing = "\n".join(
            f"- {item.name}: {item.reason}"
            for item in report.missing
        )
        return (
            "Some tasks are blocked.\n\n"
            f"{format_plan(state)}\n\n"
            f"Unmet requirements:\n{missing}\n\n"
            "Provide a final answer "
            "explaining completed work, blocked tasks, "
            "and unmet requirements without claiming they passed."
        )

    lines = [
        "RUNTIME COMPLETION GUARD:",
        "The task cannot finish yet.",
        "",
        "Missing requirements:",
    ]

    for item in report.missing:
        lines.append(
            f"- {item.name}: "
            f"{item.reason}"
        )

    lines.append("")
    lines.append(
        "Complete the missing verification "
        "requirements before finishing."
    )

    return "\n".join(lines)
