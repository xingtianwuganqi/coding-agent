"""Command line entrypoint for the mini coding agent."""

from .agent import run_agent
import argparse


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
    parser = argparse.ArgumentParser(description="Mini Coding Agent")
    parser.add_argument("--checkpoint", help="Checkpoint JSON path for this task")
    parser.add_argument("--resume", action="store_true", help="Resume saved safe checkpoint")
    args = parser.parse_args()
    if args.resume:
        if not args.checkpoint:
            parser.error("--resume requires --checkpoint")
        result = run_agent("", checkpoint_path=args.checkpoint, resume=True)
        print("\nAgent >\n" + result)
        return 

    print("Mini Coding Agent")
    print("输入 :send 发送任务")
    print("输入 exit 后再输入 :send 退出\n")

    while True:
        task = read_task()

        if task in {"exit", "quit"}:
            break

        if not task:
            continue

        result = run_agent(task, checkpoint_path=args.checkpoint)

        print("\nAgent >")
        print(result)


if __name__ == "__main__":
    main()
