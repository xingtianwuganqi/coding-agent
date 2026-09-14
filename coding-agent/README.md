# coding-agent 学习笔记

一个在终端里运行的迷你编程 Agent。它可以理解自然语言任务，并通过调用
工具来查看和修改当前工作区里的文件。今天的目标是从零把它搭起来，顺便
搞清楚一个 Agent 到底由哪些部分组成。

## 今天做了什么

- 跑通了第一个大模型调用，理解了「对话 + 指令」的基本用法。
- 搭建了一个带工具调用的 Agent 主循环。
- 给 Agent 加上文件读写、命令执行、任务计划这几类工具。
- 加了权限控制和工作区沙箱，避免 Agent 越权操作。
- 用 pytest 写了小 demo 来验证「Agent 改完代码要能跑测试」。

## 核心概念

### 1. 大模型调用（Responses API）

后端用的是 OpenAI 兼容接口，配置指向 DeepSeek：

```python
client = OpenAI(api_key=..., base_url="https://api.deepseek.com")

response = client.responses.create(
    model=MODEL,
    instructions=SYSTEM_PROMPT,   # 系统提示词，规定行为准则
    input=input_items,            # 对话历史
    tools=TOOLS,                  # 可用的工具列表
)
```

- `instructions` 相当于系统提示词，用来约束 Agent 的行为。
- `input` 是对话历史，Agent 每一轮都要把之前的内容带上。
- `tools` 告诉模型有哪些工具可用，模型自己决定用不用、用几次。

### 2. Agent 主循环（agent loop）

核心就是「问模型 → 执行工具 → 把结果再喂回模型」的循环：

```python
for step in range(MAX_STEPS):
    response = client.responses.create(...)

    # 保留模型这一轮的输出，下一轮它需要知道自己做过什么
    input_items.extend(response.output)

    tool_calls = [item for item in response.output if item.type == "function_call"]

    # 没有工具调用 = 模型认为任务完成了
    if not tool_calls:
        return response.output_text

    for call in tool_calls:
        result = execute_tool(call.name, json.loads(call.arguments), state)
        input_items.append({
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": result,
        })
```

几个关键点：

- **对话历史必须完整保留**，否则模型会「失忆」，不知道自己刚做过什么。
- 工具的返回值要带上 `call_id`，模型才能把结果和它的请求对应起来。
- 用 `MAX_STEPS` 限制最大步数，防止 Agent 无限循环。
- 没有工具调用时，说明模型给出的就是最终答案。

### 3. 工具（tools）与 JSON Schema

每个工具就是一个「函数名 + 描述 + 参数 schema」：

```python
{
    "type": "function",
    "name": "read_file",
    "description": "Read the text content of a file in the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace"}
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
```

今天实现的工具：

| 工具 | 作用 | 权限 |
| --- | --- | --- |
| `list_files` | 列出目录内容 | SAFE |
| `read_file` | 读取文件内容（超过 20000 字符截断） | SAFE |
| `write_file` | 新建或整体覆盖文件 | WRITE |
| `replace_text` | 精确替换文本，多处匹配时拒绝 | WRITE |
| `run_command` | 在工作区里执行命令 | SAFE |
| `set_plan` | 创建/替换任务计划 | SAFE |
| `update_task` | 更新某个任务的状态 | SAFE |
| `get_plan` | 查看当前计划 | SAFE |

### 4. 工作区沙箱（workspace sandbox）

所有路径都相对于工作区根目录解析，并检查是否越界：

```python
def resolve_path(path: str) -> Path:
    target = (WORKSPACE / path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"Path {path} is outside the workspace.")
    return target
```

这样 Agent 只能碰工作区内的文件，`../` 之类的路径会被拒绝。

### 5. 权限控制（permission）

把操作分成四个等级，命令和工具都走同一套判断：

- `SAFE`：直接执行（例如 `ls`、`pwd`、`git status`、`pytest`）。
- `WRITE`：写操作。
- `CONFIRM`：需要用户确认（例如 `git commit`、`uv add`）。
- `BLOCKED`：直接拒绝（例如 `rm`、`sudo`，以及含 `&&`、`|`、`>` 等的命令）。

```python
class Permission(Enum):
    SAFE = "safe"
    WRITE = "write"
    CONFIRM = "confirm"
    BLOCKED = "blocked"
```

`run_command` 先用 `classify_command` 判断，再决定放行、询问还是拒绝。

### 6. 任务计划 / 状态（task_state）

用数据类保存 Agent 的 todo 列表和每个任务的状态：

```python
class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

@dataclass
class TodoItem:
    id: int
    content: str
    status: TaskStatus = TaskStatus.PENDING
    note: str = ''
```

系统提示词要求 Agent：多步任务先 `set_plan`，动手前把任务标成
`in_progress`，完成后标成 `completed`，做不了就标成 `blocked` 并写明原因。

### 7. 交互式 CLI

支持一次输入多行任务，用 `:send` 发送，输入 `exit` / `quit` 退出：

```python
while True:
    task = read_task()
    if task in {'exit', 'quit'}:
        break
    result = run_agent(task)
    print(result)
```

## 项目结构

```
src/coding_agent/
├── main.py          # 工具定义、Agent 主循环、CLI 入口
├── permission.py    # 命令分级与安全判断
├── task_state.py    # 任务计划的数据结构
└── demo/            # 用来验证「改代码 → 跑测试」的小样例
    ├── calculator.py
    └── test_calculator.py
```

## 运行方式

```bash
uv run coding-agent     # 启动 Agent
uv run pytest           # 跑测试
```

## 今天学到的几点

1. Agent = 模型 + 工具 + 循环。模型负责决策，工具负责动手，循环负责把结果串起来。
2. 提示词（system prompt）是行为规范，很多时候比代码更能决定 Agent 靠不靠谱。
3. 让模型自己选工具、自己决定步数，比写死流程灵活得多。
4. 安全边界必须自己加：路径沙箱、命令白名单/黑名单、危险操作要确认。
5. 结构化的状态（todo + status）能让 Agent 在多步任务里保持条理。
6. `MAX_STEPS` 这类上限是防止失控的基本保险。

## 待改进

- API key 目前是硬编码的，应该改成从环境变量读取。
- `run_command` 的工具权限被标成 `SAFE`，但命令是否安全应交给
  `classify_command` 统一判断，这两处逻辑需要统一。
- demo 目录里的测试还没纳入正式的测试目录。

---

# 2026-09-12 学习记录（新增）

> 下面是今天新学到的内容，追加在后面；上面的旧笔记原样保留，没有删改。

## 今天做了什么

- 跑通了第一次大模型调用（`src/coding_agent/test.py`），确认「指令 + 输入」的最小用法。
- 给 Agent 加上了「证据（evidence）」机制：任务必须拿到证据才算完成。
- 把 `run_command` 的返回值从拼接字符串改成了结构化的 `CommandResult`。
- 细化了命令的权限分级，git / uv 按子命令分别判断。
- 用 demo 目录完整走了一遍「改代码 → 跑测试 → 看 diff」的流程，并把
  `calculator.py` / `test_calculator.py` 精简成只保留 `add`。

## 核心概念（补充）

### 8. 证据机制（evidence）：让「完成」有依据

光让模型说「我做完了」是不够的，要能验证，所以给任务加了一个
`required_evidence` 字段：

```python
class EvidenceType(Enum):
    TESTS_PASSED = "tests_passed"      # 测试真的跑过了
    DIFF_INSPECTED = "diff_inspected"  # diff 真的看过了
```

计划里的任务可以声明自己需要什么证据：

```python
set_plan(items=[
    {"content": "改代码", "required_evidence": "diff_inspected"},
    {"content": "跑测试", "required_evidence": "tests_passed"},
])
```

而证据不是模型自己声明的，是 Agent 真正执行命令之后记录下来的：

```python
def record_command_evidence(state, result):
    if result.returncode != 0:
        return                        # 命令失败，不产生证据
    args = shlex.split(result.command)
    if args[0] == "pytest":
        state.evidence.add(EvidenceType.TESTS_PASSED)
```

于是 `update_task(..., status="completed")` 时会先查证据，证据没到手
就不允许标记完成：

```python
if new_status == TaskStatus.COMPLETED:
    required = todo.required_evidence
    if required is not None and required not in state.evidence:
        return f"Cannot complete task {task_id}. Required evidence is missing: ..."
```

**关键点**：证据来自执行产生的副作用，而不是来自模型的自我描述。
这样就基本杜绝了「假装跑过测试」。

### 9. 结构化的命令结果（CommandResult）

原来 `run_command` 返回一个拼好的字符串，现在返回数据类：

```python
@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int
```

好处是调用方可以按字段判断（比如 `returncode != 0` 就不记证据），
要发给模型时再用 `format_command_result()` 格式化回文本。

### 10. 完成度判定（CompletionStatus）

模型说完了，不代表任务真的完了。所以收尾前先算一次状态：

```python
def get_completion_status(state) -> CompletionStatus:
    if not state.todos:
        return CompletionStatus.COMPLETE
    if get_unfinished_tasks(state):
        return CompletionStatus.INCOMPLETE
    if has_blocked_tasks(state):
        return CompletionStatus.BLOCKED
    return CompletionStatus.COMPLETE
```

主循环里对应三种处理：

- `INCOMPLETE`：把当前计划再喂回模型，提示「还没做完，继续」。
- `BLOCKED`：先给模型一次解释的机会（`allow_blocked_final`），再收尾。
- `COMPLETE`：正常结束，返回最终答案。

这样 Agent 就不会提前「交卷」。

### 11. 更细的命令权限分级

命令分级从一张总表拆成了按子命令判断：

```python
def classify_git_command(args):
    safe = {"status", "log", "diff", "show", "branch"}
    confirm = {"add", "commit", "checkout", "push", "pull", ...}
    ...
```

- `git status / log / diff / show / branch` → `SAFE`
- `git add / commit / push / pull / reset ...` → `CONFIRM`（要用户点头）
- `uv run pytest` → `SAFE`；`uv add / remove / sync` → `CONFIRM`
- 含 `&&`、`||`、`;`、`|`、`>`、`<` 等 shell 操作符 → 一律 `BLOCKED`

先 `shlex.split` 解析再判断，比按字符串做黑名单可靠得多。

### 12. 最小的大模型调用（test.py）

抛开 Agent 框架，最小的一次调用就四行：

```python
response = client.responses.create(
    model="deepseek-v4-flash",
    instructions="You are a helpful coding assistant.",
    input="用一句话解释什么是agent",
)
print(response.output_text)
```

没有工具、没有循环，只有「指令 + 输入」。先把这个跑通，
再加上循环和工具，Agent 才好排查问题。

## 项目结构（更新）

```
src/coding_agent/
├── main.py          # 工具定义、Agent 主循环、证据记录、CLI 入口
├── permission.py    # 命令分级（git / uv 子命令级别）
├── task_state.py    # 计划、状态、证据、命令结果的数据结构
├── test.py          # 第一次大模型调用的最小示例
└── demo/
    ├── calculator.py        # 只保留 add
    └── test_calculator.py   # 只保留 test_add
```

## demo 精简说明（2026-09-12）

按要求把 demo 里的其他方法删掉，只留下 `add`：

- `calculator.py`：只保留 `add(a, b)`，删掉 `subtract` / `multiply` / `divide`。
- `test_calculator.py`：只保留 `test_add`，删掉其他测试和多余的 `import pytest`。

改完用 `uv run pytest` 验证，结果 `1 passed`。

## 今天学到的几点（补充）

1. 「可验证」比「看起来做完了」重要：给任务绑定证据，让「完成」变成可检查的事。
2. 证据必须在执行现场产生（看 returncode），不能靠模型自述。
3. 返回值用数据类而不是字符串，判断逻辑会清爽很多。
4. 主循环的退出条件值得单独抽成一个函数（`get_completion_status`）。
5. 权限判断先解析（`shlex`）再分级，比字符串黑名单稳。
6. 删掉方法后要同步删掉对应的测试——测试才是改动是否安全的保险。

## 待改进（新增）

- `required_evidence` 目前只有两种，可以补上 `BUILD_PASSED`、`LINT_PASSED` 等。
- 证据靠命令名匹配（`args[0] == "pytest"`）比较脆，可以改成由工具主动上报。
- `allow_blocked_final` 只能兜一次，反复 blocked 时的行为还需要再想清楚。
- `ask_for_confirmation` 里还留着 `print(f"DEBUG answer: ...")`，调试完应该删掉。
