from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class TraceEvent:
    '''
    跟踪事件
    '''
    # 轮数
    turn: int
    # 时间类型
    event_type: int
    # tool name
    name: str
    # 详情
    detail: str = ""
    # 时间
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = (
                datetime.now().isoformat(
                    timespec="seconds"
                )
            )


@dataclass
class AgentTrace:
    '''
    Agent跟踪
    '''

    events: list[TraceEvent] = field(
        default_factory=list
    )

    def record(
            self,
            turn: int,
            event_type: str,
            name: str,
            detail: str = "",
    ) -> None:

        self.events.append(
            TraceEvent(
                turn=turn,
                event_type=event_type,
                name=name,
                detail=detail
            )
        )


    def print_trace(self) -> None:

        print("\n===== Agent Run Trace =====")

        for event in self.events:

            print(
                f"[Turn {event.turn}] "
                f"{event.event_type.upper()} "
                f"{event.name}"
            )

            if event.detail:
                print(
                    f"    {event.detail}"
                )

        print("===========================\n")
