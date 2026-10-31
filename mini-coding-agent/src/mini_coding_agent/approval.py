"""Lesson 26: one policy decision and one explicit approval per tool invocation.

This is a teaching-grade gate, NOT an OS sandbox. Tool implementations
still need independent path checks; untrusted subprocesses need isolation.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import PurePath
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .permission import (
    Permission, TOOL_PERMISSIONS, classify_command, ask_for_confirmation,
)


class ApprovalStatus(str, Enum):
    ALLOWED = "allowed"
    APPROVED = "approved"
    DENIED = "denied"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ApprovalDecision:
    status: ApprovalStatus
    reason: str
    signature: str

    @property
    def allowed(self) -> bool:
        return self.status in {ApprovalStatus.ALLOWED, ApprovalStatus.APPROVED}


# Limited, parsed forms that can be run without a confirmation prompt.
# This is a convenience policy for a trusted local workspace, not a sandbox.
_GIT_DIFF_OPTIONS = {
    "--stat", "--name-only", "--name-status", "--cached", "--staged",
    "--check", "--no-ext-diff", "--no-textconv", "--color=never",
}
_GIT_STATUS_OPTIONS = {
    "--short", "-s", "--branch", "-b", "-sb", "-bs",
    "--porcelain", "--porcelain=v1", "--porcelain=v2",
    "--untracked-files=all", "--untracked-files=normal",
}


def _local_operand(token: str) -> bool:
    """Keep automatic approval to simple relative paths (not a path sandbox)."""
    path = PurePath(token)
    return bool(token) and not path.is_absolute() and ".." not in path.parts and not token.startswith("~")


def _git_readonly_args(args: list[str], allowed_options: set[str]) -> bool:
    paths_only = False
    for item in args:
        if not paths_only and item == "--":
            paths_only = True
        elif not paths_only and item.startswith("-"):
            if item not in allowed_options:
                return False
        elif not _local_operand(item):
            return False
    return True


def is_auto_approved_readonly_command(command: str) -> bool:
    """Approve simple read-only command *forms*, not exact command strings.

    Command syntax / prohibited shell constructs are checked separately by
    permission.classify_command(), and subprocess.run uses shell=False.
    """
    try:
        args = shlex.split(command)
    except ValueError:
        return False

    if args == ["pwd"]:
        return True

    if not args:
        return False

    if args[0] == "ls":
        # ls cannot modify files, but avoid auto-approving explicit outside paths.
        paths_only = False
        for item in args[1:]:
            if not paths_only and item == "--":
                paths_only = True
            elif not paths_only and item.startswith("-"):
                continue
            elif not _local_operand(item):
                return False
        return True

    if args[:2] == ["git", "status"]:
        return _git_readonly_args(args[2:], _GIT_STATUS_OPTIONS)

    if args[:2] == ["git", "diff"]:
        return _git_readonly_args(args[2:], _GIT_DIFF_OPTIONS)

    return False


def policy_for_tool(name: str, arguments: dict) -> Permission:
    policy = TOOL_PERMISSIONS.get(name, Permission.BLOCKED)
    if policy == Permission.BLOCKED:
        return policy

    if name != "run_command":
        return policy

    command = arguments.get("command")
    if not isinstance(command, str):
        return Permission.BLOCKED

    original = classify_command(command)
    if original != Permission.SAFE:
        return original  # CONFIRM or BLOCKED

    if is_auto_approved_readonly_command(command):
        return Permission.SAFE

    return Permission.CONFIRM


def approval_signature(name: str, arguments: dict) -> str:
    """Exact call signature. A denial persists for this call in one RunSession."""
    content = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def approval_detail(name: str, arguments: dict) -> str:
    if name == "delete_file":
        return f"Target file: {arguments.get('path', '<unknown>')}"
    if name == "run_command":
        return f"Command: {arguments.get('command', '<missing>')}"
    return "Arguments: " + json.dumps(arguments, ensure_ascii=False, default=str)


def authorize_tool_call(
    name: str,
    arguments: dict,
    *,
    denied_signatures: set[str] | None = None,
    confirm: Callable[[str, str | None], bool] | None = None,
) -> ApprovalDecision:
    """Fail closed: only a positive y/yes approves a CONFIRM tool.

    `denied_signatures` is scoped to the current RunSession; a previous
    rejection of exactly the same call must not re-prompt the user.
    """
    signature = approval_signature(name, arguments)
    policy = policy_for_tool(name, arguments)
    if policy == Permission.BLOCKED:
        return ApprovalDecision(
            ApprovalStatus.BLOCKED, f"POLICY_BLOCKED: {name}", signature
        )
    if policy in {Permission.SAFE, Permission.WRITE}:
        return ApprovalDecision(ApprovalStatus.ALLOWED, "Allowed by policy", signature)

    if denied_signatures is not None and signature in denied_signatures:
        return ApprovalDecision(
            ApprovalStatus.DENIED,
            "USER_DENIED: this exact operation was already rejected in this run.",
            signature,
        )

    if confirm is None:
        confirm = ask_for_confirmation
    try:
        approved = confirm(name, approval_detail(name, arguments))
    except (EOFError, KeyboardInterrupt):
        approved = False
    if approved:
        return ApprovalDecision(ApprovalStatus.APPROVED, "Explicit user approval", signature)

    if denied_signatures is not None:
        denied_signatures.add(signature)
    return ApprovalDecision(
        ApprovalStatus.DENIED,
        "USER_DENIED: user denied or did not approve the operation.",
        signature,
    )
