from dataclasses import dataclass, field
from enum import Enum

class CompletionStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class EvidenceType(Enum):
    """
    证据类型，以后还能扩展，现在只做两个
    BUILD_PASSED
    LINT_PASSED
    TYPE_CHECK_PASSED
    FILE_READ
    USER_APPROVED
    DEPLOY_SUCCEEDED
    """
    TESTS_PASSED = "tests_passed"
    DIFF_INSPECTED = "diff_inspected"

@dataclass
class TodoItem:
    id: int
    content: str
    status: TaskStatus = TaskStatus.PENDING
    note: str = ''
    required_evidence: EvidenceType | None = None

@dataclass
class AgentState:
    todos: list[TodoItem] = field(default_factory=list)
    evidence: set[EvidenceType] = field(default_factory=set)


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int