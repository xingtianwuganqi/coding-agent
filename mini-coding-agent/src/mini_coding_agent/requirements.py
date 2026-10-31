'''任务需求提取'''

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from collections.abc import Callable
from .tool_result import ToolResult
from .config import WORKSPACE
import re


if TYPE_CHECKING:
    from .planning import AgentState

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

# 没有扩展名，但名曲属于文件的常见名称
EXTENSIONLESS_FILES = {
    "Makefile",
    "Dockerfile",
    "README",
    "LICENSE",
    "COPYING",
    "CONTRIBUTING",
    "CHANGELOG",
    "INSTALL",
    "TODO",
    ".gitignore",
    ".env",
}

def normalize_file_target(
        target: str,
) -> str:
    '''
    将 Requirement Target 规范化为项目相对文件路径

    V1 仅支持文件级target， 不支持函数名或者类名
    参数：
        target: 模型传入的文件路径

    返回：
        规范化后的项目相对文件路径

    异常：
        ValueError: 如果 target 不是有效的文件路径
    '''

    # 1. 类型检查
    if not isinstance(target, str):
        raise ValueError(
            f"Requirement target must be a string"
        )

    # 2. 去除空格，同一路径分隔符
    target = target.strip().replace("\\", "/")

    # 兼容模型常见的 "./" 前缀写法
    while target.startswith("./"):
        target = target[2:]

    if not target:
        raise ValueError(
            f"Requirement target cannot be empty"
        )

    # 3. 不允许绝对路径
    if target.startswith("/") or target.startswith("~") or re.match(r"^[a-zA-Z]:", target):
        raise ValueError(
            f"Requirement target must be a relative path"
        )

    # 4. 不允许父目录跳转
    parts = target.split("/")
    if any(part in ("..", ".", "") for part in parts):
        raise ValueError(
            f"Requirement target contains invalid path segments: {target}"
        )

    path = PurePosixPath(target)
    # 5. 拦截单独的函数名等非文件目标
    # 注意：这是 V1的约束， 不是通用文件识别算法
    if len(path.parts) == 1 and path.suffix == "" and path.name not in EXTENSIONLESS_FILES:
        raise ValueError(
            f"Invalid file target: '{target}'. "
            "Expected a file path, not a symbol name."
        )

    return path.as_posix()


# 搜索裸文件名时跳过的目录：第三方依赖/缓存中的同名文件
# 不应参与唯一匹配判断。
IGNORED_TARGET_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
}

MAX_REPORTED_TARGET_MATCHES = 10

# 当裸文件名命中多个文件时，由调用方决定如何选择：
# resolver(target, matches) -> 选中的工作区相对路径，或 None 表示未选择
TargetResolver = Callable[[str, list[str]], str | None]


def find_workspace_files_by_name(
        name: str,
) -> list[str]:
    '''
    在工作区内按文件名查找文件，返回项目相对路径列表。
    '''
    matches = []

    for path in WORKSPACE.rglob("*"):
        if not path.is_file():
            continue

        if path.name != name:
            continue

        relative = path.relative_to(WORKSPACE)

        if any(
            part in IGNORED_TARGET_DIRS
            for part in relative.parts[:-1]
        ):
            continue

        matches.append(relative.as_posix())

    return sorted(matches)


def resolve_file_target(
        target: str,
        resolver: TargetResolver | None = None,
) -> str:
    '''
    将 Requirement Target 解析为工作区相对文件路径。

    - 已经包含目录的路径：只做 normalize_file_target 的语法校验；
    - 裸文件名：在工作区内按文件名查找，
      唯一命中时补全为完整相对路径；
      没有命中时校验失败；
      命中多个时调用 resolver（通常用 ask_user 让用户选择），
      未提供 resolver 或用户未选择时校验失败。
    '''
    normalized = normalize_file_target(target)

    # 已包含目录，视为完整相对路径
    if "/" in normalized:
        if (WORKSPACE / normalized).is_dir():
            raise ValueError(
                f"Target is a directory, not a file: '{target}'. "
                "must_change requires a file path."
            )
        return normalized

    matches = find_workspace_files_by_name(normalized)

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise ValueError(
            f"File not found in workspace: '{target}'. "
            "Provide the complete workspace-relative file path."
        )

    shown = matches[:MAX_REPORTED_TARGET_MATCHES]
    suffix = ""

    if len(matches) > len(shown):
        suffix = f" (and {len(matches) - len(shown)} more)"

    listing = f"{', '.join(shown)}{suffix}"

    # 命中多个文件：交给 resolver（ask_user）选择
    if resolver is not None:
        selected = resolver(target, matches)

        if selected in matches:
            return selected

        raise ValueError(
            f"Ambiguous file target: '{target}' matches multiple "
            f"files: {listing}. No file was selected. "
            "Provide the complete workspace-relative file path."
        )

    raise ValueError(
        f"Ambiguous file target: '{target}' matches multiple "
        f"files: {listing}. "
        "Provide the complete workspace-relative file path."
    )


def validate_requirement_payloads(
        items: list[dict],
        resolver: TargetResolver | None = None,
) -> list[dict]:
    '''
    校验并规范化模型传入的 Requirements。
    校验失败直接抛出 ValueError.
    不允许进入正式 Requirement 状态
    '''
    validated = []
    for index, item in enumerate(items, start=1):

        # 避免原地修改模型传入的字典
        normalized_item = item.copy()
        kind = normalized_item.get("kind")
        target = normalized_item.get("target")

        # V1 先只约束 MUST_CHANGE 
        if kind == RequirementKind.MUST_CHANGE.value:
            # 没有指定target 时，保留旧版本通用修改语义
            if target is not None and target != "":
                try:
                    normalized_target = resolve_file_target(
                        target,
                        resolver=resolver,
                    )
                    normalized_item["target"] = normalized_target
                except ValueError as exc:
                    raise ValueError(
                        f"Requirement {index} has invalid target: {exc}"
                    )

        validated.append(normalized_item)
    return validated


def set_requirements(
    state: AgentState,
    requirements: list[dict],
    resolver: TargetResolver | None = None,
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

    # 获取模型传入的数据，在状态发生修改前进行校验：
    try:
        validated_requirements = validate_requirement_payloads(
            requirements,
            resolver=resolver,
        )
    except ValueError as exc:
        return ToolResult.fail(
            error=(
                f"REQUIREMENT_VALIDATION_ERROR: {exc}\n"
                "Use a workspace-relative file path "
                "instead of a function or class name."
            )
        )
        # return (
        #     f"REQUIREMENT_VALIDATION_ERROR: {exc}\n"
        #     "Use a workspace-relative file path "
        #     "instead of a function or class name."
        # )

    parsed: list[TaskRequirement] = []

    for index, item in enumerate(validated_requirements):
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
        state: AgentState,  # 当前代理状态
) -> str:
    """
    构建任务需求上下文。

    参数:
        state (AgentState): 代理的状态，包含锁定的需求信息。

    返回:
        str: 表示任务需求的字符串，如果需求未被提取则返回错误消息。
    """

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


def normalize_path(
    path: str,
) -> str:
    '''
    将路径规范化为 Posix 写法的项目相对路径（宽松版，不报错）

    参数：
        path: 任意待规范化的路径字符串

    返回：
        规范化后的路径：
        - 反斜杠 "\\" 统一替换为 "/"
        - 去掉开头的 "./"

    说明：
        这里只做字符串层面的统一，不做合法性校验；
        需要严格校验文件目标时请使用 normalize_file_target
    '''

    # 统一分隔符为 "/"（PurePosixPath 会把 "\\" 当普通字符，需先替换）
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
    '''
    判断一个路径是否命中 Requirement Target（文件或目录）

    语义：
    - 命中 = 两者完全相同，或 target 是 path 的某一级父目录
    - target 可以是文件路径，也可以是目录路径；
      目录路径匹配其自身以及其下所有子文件/子目录
    - 匹配是前缀 + 边界判定，而不是纯字符串前缀：
      target="src/app" 能命中 "src/app/main.py"，
      但不会命中 "src/app2/main.py"（按路径段比较，避免误判）

    参数：
    - path：待检查的路径（通常是实际修改的文件路径），项目相对路径
    - target：需求中声明的目标路径，项目相对路径

    返回值：
    - True：path 等于 target，或 path 位于 target 目录之下
    - False：不匹配

    实现步骤：
    1. 先把两个路径规范化，消除 "./" 前缀、反斜杠、".." 等写法差异，
       使 "auth.py"、"./auth.py"、"src\\auth.py" 等形式可以统一比较
    2. 用 PurePosixPath 按路径段（而不是按字符）进行比较
    3. 完全相等直接命中
    4. 否则检查 target 是否出现在 path 的各级父目录中，
       等价于“path 位于 target 目录之下”
    '''

    # 统一两个路径的写法，避免因 "./"、反斜杠或多余分隔符导致误判
    normalized_path = normalize_path(path)

    normalized_target = normalize_path(target)

    # 转成 PurePosixPath，使后续比较基于路径段（parts）而不是原始字符串
    path_obj = PurePosixPath(
        normalized_path
    )

    target_obj = PurePosixPath(
        normalized_target
    )

    # 情况一：路径完全相同，直接命中
    if path_obj == target_obj:
        return True

    # 情况二：target 是 path 的父目录之一，说明 path 在该目录之下
    # 注意：parents 是按路径段比较的，因此 "src/app" 不会命中 "src/app2/x.py"
    return target_obj in path_obj.parents



def path_matches_file_target(
        changed_path: str,
        target: str,
) -> bool:
    '''
    判断实际修改的文件是否等于 Requirement Target

    两个参数都要求使用项目相对路径
    '''
    try:
        normalized_changed_path = normalize_file_target(
            changed_path
        )
        normalized_target = normalize_file_target(
            target
        )
    except ValueError:
        return False

    return normalized_changed_path == normalized_target



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
    """
    判断某条 MUST_VERIFY 需求是否已被满足。

    判定规则：

    1. 只认可 ``record.success is True`` 的验证记录；
    2. 记录类型必须与需求的 ``verifier`` 一致；
    3. 记录必须对应当前代码/工作区版本：

       - ``verifier == "diff"`` 时比较
         ``workspace_revision``；
       - 其他类型比较 ``code_revision``；

       版本不一致说明验证结果已过期，不予认可。

    4. 若需求指定了 ``command_contains``，则要求
       验证记录的 ``source`` 中包含该命令片段
       （大小写不敏感）。

    从最新的记录往前遍历，一旦找到满足上述全部
    条件的记录即返回 True；全部检查完仍未找到则
    返回 False。
    """

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
    '''
    评估单条 Requirement 是否满足
    '''
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

        # 指定了 target
        # V1 要求其必须是文件路径

        try:
            normalized_target = normalize_file_target(
                requirement.target
            )
        except ValueError as exc:

            return RequirementCheck(
                requirement_id=requirement.id,
                satisfied=False,
                reason=(
                    f"Invalid requirement target: "
                    f"{requirement.target}. "
                    f"Error: {exc}"
                )
            )

        # satisfied = any(
        #     path_matches_target(
        #         path=changed_path,
        #         target=requirement.target,
        #     )
        #     for changed_path in state.changed_files
        # )
        satisfied = any(
            path_matches_file_target(
                changed_path=changed_path,
                target=normalized_target,
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
