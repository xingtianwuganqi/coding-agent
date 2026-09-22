from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool

    content: str = ""

    error: str = ""

    return_code: int | None = None

    @classmethod
    def ok(
        cls,
        content: str = "",
        return_code: int | None = None,
    ) -> "ToolResult":

        return cls(
            success=True,
            content=content,
            return_code=return_code,
        )

    @classmethod
    def fail(
        cls,
        error: str,
        content: str = "",
        return_code: int | None = None,
    ) -> "ToolResult":

        return cls(
            success=False,
            content=content,
            error=error,
            return_code=return_code,
        )