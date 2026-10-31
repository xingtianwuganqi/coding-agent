'''运行时上下文渲染（非 plan 逻辑，从旧 planning 前身模块拆出）'''

from __future__ import annotations

from .planning import AgentState
from .requirements import build_requirement_context
from .task_state import get_ready_tasks

def build_goal_context(
        state: AgentState
) -> str:

    if not state.goal.locked:
        return (
            "GOAL:\n"
            "Not established yet. "
        )

    return (
        "LOCKED GOAL: \n"
        f"{state.goal.objective}\n\n"
        "The goal cannot be changed "
        "during replanning"
    )


def build_plan_context(
        state: AgentState,
) -> str:

    if state.plan.revision == 0:
        return (
            "ACTIVE PLAN: \n"
            "No plan exists yet."
        )

    lines = [
        (
            "ACTIVE PLAN "
            f"[revision "
            f"{state.plan.revision}]:"
        )
    ]

    for todo in state.plan.items:
        line = (
            f"- [{todo.status.value}]"
            f"{todo.id}"
            f"{todo.content}"
        )

        if todo.depends_on:
            deps = ", ".join(str(dep_id) for dep_id in todo.depends_on)
            line += f" [depends_on: {deps}]"

        ready_ids = [
            todo.id
            for todo in get_ready_tasks(state)
        ]

        line += (
            f"\n READY TASK IDS: {ready_ids}"
        )

        lines.append(line)


    if state.plan.replan_count > 0:
        lines.append("")
        lines.append(
            "Previous plan revisions: "
            f"{state.plan.replan_count}"
        )


    return "\n".join(lines)


def build_runtime_instructions(
        state: AgentState,
) -> str:

    parts = [
        build_goal_context(
            state
        ),
        build_requirement_context(
            state,
        ),
        build_plan_context(
            state,
        )
    ]

    return "\n\n".join(
        part
        for part in parts
        if part
    )


def print_state_debug(
    state: AgentState,
) -> None:
    print("\n--- Runtime State ---")

    print(
        "Workspace revision:",
        state.workspace_revision,
    )

    print("Evidence:")

    if not state.evidence:
        print("  (none)")
        return

    for evidence_type, revision in (
        state.evidence.items()
    ):
        valid = (
            revision
            == state.workspace_revision
        )

        print(
            f"  {evidence_type.value}: "
            f"revision={revision}, "
            f"valid={valid}"
        )
