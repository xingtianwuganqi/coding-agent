# 总结

## 1.工具调用 Tool Calling
```
1. list_files
2. read_file
3. write_file
4. replace_text
5. run_command
```

## 2.权限控制 Tool Permission / Human in the loop


### 2.1 工具权限控制
### 2.2 AI 自动执行命令控制
```
1.TOOL_PERMISSION
2.Permission
3.classify_command
4.在调用工具或者执行命令时，提前询问

```
### 2.3 Command Allowlist
```
1.SHELL_OPERATORS
2.classify_git_command、classify_uv_command
2.禁止直接调用shell命令，而是单独调用git、uv命令，然后分开判断git、uv命令，再单独判断权限
```

## 3.planing

### 3.1 先新增TaskStatus、TodoItem、AgentState, AgentState是一次任务的总的状态

### 3.2 新增set_plan,update_task,get_plan的Tool和实现，

### 3.3 Agent loop 中，AI制定计划，通过set_plan转化成Agent runtime中可观察的计划状态,完成计划后，ai调用update_task,更新计划状态

## 4.Task Completion Guard.不再完全相信模型说“任务完成了”，而是由 Runtime 检查 Todo 是否真的全部完成

### 4.1 之前没有工具调用直接结束，现在开始判断任务中是否有未完成的任务

### 4.2定义完成主要看todos中的任务是否都完成了，都完成了，就结束，没有完成或者有禁止的就继续
```
1.CompletionStatus
2.get_completion_status
3. 判断是否结束：
completion_status = get_completion_status(
                state
            )

            if completion_status == CompletionStatus.COMPLETE:
                return message.content or ""

            if (
                completion_status == CompletionStatus.BLOCKED
                and allow_blocked_final == True
            ):
                return message.content or ""


            if completion_status == CompletionStatus.INCOMPLETE:
                unfinished = get_unfinished_tasks(state)

                unfinished_text = "\n".join(
                    f"- {todo.id}. {todo.content}"
                    for todo in unfinished
                )

                input_items.append(
                    {
                        "role": "user",
                        "content": (
                            "You attempted to finish the task, "
                            "but the todo plan is not complete.\n\n"
                            "Remaining tasks:\n"
                            f"{unfinished_text}\n\n"
                            "Continue working on the remaining tasks. "
                            "Do not provide a final answer yet."
                        ),
                    }
                )

                continue

            if completion_status == CompletionStatus.BLOCKED:
                allow_blocked_final = True
                input_items.append(
                    {
                        "role": "user",
                        "content": (
                            "Some tasks are blocked.\n\n"
                            f"{format_plan(state)}\n\n"
                            "Provide a final answer "
                            "explaining completed work "
                            "and blocked tasks."
                        ),
                    }
                )

                continue
```

## 5 Evidence Guard：确保任务是实际完成了，而不只是状态改变

### 5.1 新增EvideceType，修改set_plan,现在set_plan不止返回plan list[str]了，还会携带是否需要tests_passed, 需要test的，最后执行完以后，runtime会验证是否执行成功，returncode是不是0，是0就是命令执行通过了，最后在给llm看
```
1.EvideceType
2.set_plan
3.execute_tool中（record_command_result、format_command_result）
4.update_task()
如果当前状态完成了，
如果这个 Todo 有证据要求：required = todo.required_evidence
但runtime中没有记录required not in state.evidence
直接拒绝

```

### 5.2 Evidence 必须由受信任的 Runtime 根据 Tool 执行结果产生。
```
run_command("uv run pytest")
↓
真实 subprocess
↓
returncode == 0
↓
Runtime 自动记录 TESTS_PASSED
```

## 6.Evidence Invalidation / Dirty State（证据失效 / 脏状态）只要代码发生修改，就删除旧的测试证据和diff证据

### 6.1 state中的evidence有值之后，在write_file和replace_text后，代码发生了变化，要清空掉state中的evidence，让再次进行tests和diff
```
1.invalidate_evidence
2.FileOperationResult
3.write_file和replace_text返回FileOperationResult
4.execute_tool后，如果changed，则要清空state的evidence，下一次会再次验证

```

## 7.Versioned Evidence（带版本的证据）。

### 7.1 给AgentState添加workspace_revision，并且将AgentState中的evidence改成dict，当执行write_file或者replace_text后，会将workspace_revision+=1，在执行完run_command后，会根据命令更新state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision,state.evidence[EvidenceType.TESTS_PASSED]和state.workspace_revision相等，就说明当前版本更改经过了验证，如果在update_task时不想带，就告诉llm，没有执行验证命令，需要重新执行run_command,进行验证，验证之后state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision

```
1.AgentState:  workspace_revision
2.evidence: dict[EvidenceType: int] = field(default_factory=dict)
3.mark_workspace_changed
4.has_valid_evidence
5.execute_tool中mark_workspace_changed
6.run_command中record_command_result
7.update_task中not has_valid_evidence
```

## 8.Context Management(上下文治理)

### 8.1
| 东西            | 代表什么                  |
| ------------- | --------------------- |
| Workspace     | 真实世界现在是什么样            |
| AgentState    | Runtime 知道任务做到哪、证据是什么 |
| Model Context | 这一轮 LLM 能看到什么         |

Workspace 是事实源，State 是工作记忆，Context 是模型当前看到的材料。

### 8.2 将最近的数据进行保留，比如说保留8个，将，当前的AgentState进行描述，然后在传给llm

```
1.build_model_input(最近的几条数据)
2.format_runtime_state (当前runtime的状态描述)
3.runtime_instructions, runtime中对agent state的拼接和描述
4.
```



## 9.Context Compression / Task Summary（上下文压缩）

### 9.1

1. Recent Context
   最近几轮具体发生了什么

2. Runtime State
   当前做到哪、Evidence、Revision

3. Task Summary
   过去发生过但以后仍然有价值的结论
```
1.task_summary
```

### 9.2 将history_list中前端的内容和state.summary进行合并总结，然后将总结的内容拼接进build_model_input中，发送给llm
```
1.turn_to_text
2.history_to_text
3.summarize_history
4.compress_history
5.build_model_input

```

## 10.Tool Output Truncation / Large File Handling：比如 pytest 输出 5 万字符、read_file 打开一个 1MB 文件时，为什么即使只有最近 6 Turn 也会把 Context 撑爆，以及 Coding Agent 应该怎样按行读取、搜索代码和截断工具输出。

### 10.1 Tool Output Truncation 控制命令输出的大小
```
1.trncate_text、truncate_tail
2.对format_command_result中调用命令返回的结果进行裁剪，

```

### 10.2 Large File Handling 不再一次读取整个大文件，而是按行读取 + 搜索定位
```
1.修改read_file，改为按范围读取
2.新增search_text Tool
```

## 11. Context Budget / Token Budget

### 11.1 动态计算token数量.
规定好总的token数，总token数减去输出token数，安全边界token数，就是输入token数。
输入token数又可以分为静态token数，包括system_prompt,task,tools, summary, runtime_state等，还有recent_state，就是最近的内容，裁剪出来的内容，这个内容是动态的，就是输入token数减去静态token数，就是最近动态的token数，尽可能多的最近动态

```
1.estimate_tokens
2.get_input_token_budget
3.estimate_tools_tokens
4.estimate_fixed_context_tokens
5.estimate_turn_tokens
6.select_recent_turns
7.get_recent_context_budget
8.get_recent_context_budget
9.build_model_input
10.print_context_debug
```

