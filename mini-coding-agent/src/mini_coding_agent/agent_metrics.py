from dataclasses import dataclass, field
import time

@dataclass
class AgentMetrics:
    # 运行次数
    model_turns: int = 0
    # 方法调用次数
    tool_calls: int = 0

    tool_calls_by_name: dict[str, int] = field(
        default_factory=dict
    )
    # 静止工具调用次数
    blocked_tool_calls: int = 0
    # 进入压缩方法次数
    compression_checks: int = 0
    # 上下文压缩次数
    context_compressions: int = 0
    # 测试次数
    tests_run: int = 0
    # 指定计划在第几步
    plan_created_turn: int | None = None
    # 第一次写入在第几步
    first_write_turn: int | None = None
    # 开始时间
    start_time: float = field(
        default_factory=time.time
    )
    # 结束时间
    end_time: float | None = None

    # 停滞记录
    stagnation_warnings: int = 0

    # -------------------
    # 新增
    # -------------------

    # runtime实际调用工具多少次
    tool_executions: int = 0
    # 工具调用失败次数
    tool_failures: int = 0
    # 自动重试
    auto_retries: int = 0
    # 恢复失败
    recovered_failures: int = 0

    repeated_failures: int = 0

    # 给Metrics增加记录工具的方法
    def record_tool_call(
            self,
            name: str
    ) -> None:
        '''
        增加记录
        '''
        self.tool_calls += 1
        self.tool_calls_by_name[name] = (
            self.tool_calls_by_name.get(name, 0) + 1
        )


    def finish(self) -> None:
        self.end_time = time.time()


    @property
    def duration(self) -> float:
        end = (
            self.end_time
            if self.end_time is not None
            else time.time()
        )
        return end - self.start_time

    def print_summary(self) -> None:

        print("\n===== Agent Run Metrics =====")

        print(
            f"Model turns: {self.model_turns}"
        )

        print(
            f"Tool calls: {self.tool_calls}"
        )

        print("\nTool usage:")

        for name, count in sorted(
            self.tool_calls_by_name.items()
        ):
            print(
                f"  {name}: {count}"
            )

        print(
            f"\nBlocked tool calls: "
            f"{self.blocked_tool_calls}"
        )

        print(
            f"Compression checks: "
            f"{self.compression_checks}"
        )

        print(
            f"Context compressions: "
            f"{self.context_compressions}"
        )

        print(
            f"Tests executed: "
            f"{self.tests_run}"
        )

        print(
            f"Plan created at turn: "
            f"{self.plan_created_turn}"
        )

        print(
            f"First write at turn: "
            f"{self.first_write_turn}"
        )

        print(
            f"Duration: "
            f"{self.duration:.2f}s"
        )

        print(
            f"Tool executions: "
            f"{self.tool_executions}"
        )

        print(
            f"Tool failures: "
            f"{self.tool_failures}"
        )

        print(
            f"Auto retries: "
            f"{self.auto_retries}"
        )

        print(
            f"Recovered failures: "
            f"{self.recovered_failures}"
        )

        print(
            f"Repeated failures: "
            f"{self.repeated_failures}"
        )

        print("=============================\n")


    def end_runing(self):
        self.finish()
        self.print_summary()


def is_test_command(
        command: str
) -> bool:

    command = command.lower()

    test_keywords = [
        "pytest",
        "unittest",
        "npm test",
        "go test",
        "swift test",
        "npm test",
        "pnpm test",
        "yarn test",
    ]

    return any(
        keyword in command
        for keyword in test_keywords
    )