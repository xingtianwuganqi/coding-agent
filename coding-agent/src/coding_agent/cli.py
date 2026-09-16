"""Interactive command line interface."""

from .agent import run_agent

def read_task() -> str:
    print("You > ", end="", flush=True)

    lines = []

    while True:
        line = input()

        if line.strip() == ":send":
            break

        lines.append(line)

    return "\n".join(lines).strip()


def main():
    print('Mini Coding Agent')
    print("输入 :send 发送任务")
    print("输入 exit 后再输入 :send 退出\n")

    while True:
        task = read_task()
        if task in {'exit', 'quit'}:
            break

        if not task:
            continue

        result = run_agent(task)
        print("\n Agent >")
        print(result)
