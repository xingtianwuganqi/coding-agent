from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool

    content: str = ""

    error: str = ""

    return_code: int | None = None

    # 新增
    # # 状态已经改变
    # state_changed: bool = False

    # # 已经验证成功
    # verification_succeeded: bool = False

    @classmethod
    def ok(
        cls,
        content: str = "",
        return_code: int | None = None,
        # state_changed: bool = False,
        # verification_succeeded: bool = False
    ) -> "ToolResult":

        return cls(
            success=True,
            content=content,
            return_code=return_code,
            # state_changed=state_changed,
            # verification_succeeded=verification_succeeded
        )

    @classmethod
    def fail(
        cls,
        error: str,
        content: str = "",
        return_code: int | None = None,
        # state_changed: bool = False,
        # verification_succeeded: bool = False
    ) -> "ToolResult":

        return cls(
            success=False,
            content=content,
            error=error,
            return_code=return_code,
            # state_changed=state_changed,
            # verification_succeeded=verification_succeeded
        )