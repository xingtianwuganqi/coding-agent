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


## 12 重构项目

### 12.1遇到的问题，输入重构需求后，ai一直不停的阅读，不列计划，不做工作，陷入阅读循环

### 12.2 解决办法
```
1.调整SYSTEM_PROMPT,让尽快列计划开始执行
2.AgentState中新增pre_plan_inspection_count、consecutive_retrieval_count、recent_tool_calls
3.新增guard_tool_call、tool_signature方法，在execute_tool方法中添加guard_tool_call判断，如果连续调用，或者连续阅读搜索，就会告诉llm,尽快制定计划然后执行

```

## 13.Agent Observability - 运行轨迹与指标

### 13.1 AgentMetrics 数据结构

新增 `src/mini_coding_agent/agent_metrics.py`，用 `@dataclass` 定义 `AgentMetrics`，集中记录一次 agent run 的运行指标：

```
1.model_turns               模型调用轮数
2.tool_calls                工具调用总次数
3.tool_calls_by_name        按工具名分类的调用次数（dict）
4.blocked_tool_calls        被 guard 拦截的工具调用次数（含 guard_tool_call 与 guard_recent_tool_call 两处拦截）
5.context_compressions      上下文压缩次数
6.tests_run                 执行测试的次数
7.plan_created_turn         在第几轮创建计划
8.first_write_turn          在第几轮第一次写文件
9.start_time / end_time     运行起止时间
```

### 13.2 记录方法

```
1.record_tool_call(name)：tool_calls +1，并把次数累加到 tool_calls_by_name
2.finish()：记录 end_time
3.duration 属性：end_time(或当前时间) - start_time
4.print_summary()：打印 Agent Run Metrics，包括轮数、工具调用、工具使用分布、被拦截次数、压缩次数、测试次数、建计划轮次、首次写入轮次和耗时
5.end_runing()：finish() + print_summary() 的组合
6.is_test_command(command)：通过关键字（pytest / unittest / npm test / go test / swift test）判断命令是否为测试命令
```

### 13.3 与运行流程的集成

```
1.agent.run_agent 中创建 metrics = AgentMetrics()
2.while 循环每轮 metrics.model_turns += 1，超过 MAX_MODEL_TURN 时调用 metrics.end_runing() 再返回
3.agent 正常结束/异常退出时都调用 metrics.end_runing() 输出汇总
4.compress_history 接收 metrics，每次压缩 history 时 metrics.context_compressions += 1
5.ToolRunner 的 execute_tool 接收 metrics：
  1) 开头调用 metrics.record_tool_call(name) 记录工具调用
  2) guard_tool_call 或 guard_recent_tool_call 拦截时 metrics.blocked_tool_calls += 1
  3) write_file / replace_text 首次写入时记录 metrics.first_write_turn = metrics.model_turns
  4) set_plan 首次调用时记录 metrics.plan_created_turn = metrics.model_turns
  5) run_command 时用 is_test_command 判断，是测试命令则 metrics.tests_run += 1
6.以上各记录点共同形成可观测的运行轨迹
```


##  14.Agent Run Trace，Agent 运行追踪

### 14.1 数据结构

```
1.TraceEvent：单条跟踪事件
   1) turn：事件发生的轮数（对应 metrics.model_turns）
   2) event_type：事件类型，取值 model_turn / tool / blocked / blocked_recent / compression
   3) name：事件名称，如 call_model 或具体工具名
   4) detail：事件详情，默认空字符串
   5) timestamp：事件时间，未传入时由 __post_init__ 用 datetime.now() 自动生成（精确到秒）
2.AgentTrace：跟踪容器
   1) events：TraceEvent 列表，使用 field(default_factory=list) 初始化
```

### 14.2 AgentTrace 方法

```
1.record(turn, event_type, name, detail)：构造一个 TraceEvent 并追加到 events 中
2.print_trace()：打印 Agent Run Trace
   1) 先输出 "===== Agent Run Trace ====="
   2) 逐条打印 "[Turn {turn}] {EVENT_TYPE} {name}"（event_type 转大写）
   3) detail 非空时另起一行缩进打印
   4) 最后输出 "==========================="
```

### 14.3 与运行流程的集成

```
1.agent.run_agent 中创建 trace = AgentTrace()
2.while 循环每轮先 trace.record(turn=metrics.model_turns, event_type="model_turn", name="call_model")
3.trace 作为参数传给 compress_history 和 execute_tool，在内部继续记录事件
4.退出时统一调用 trace.print_trace()：达到 MAX_MODEL_TURN、任务完成（COMPLETE）、允许 BLOCKED 结束等情况
5.compress_history 接收 trace，压缩 history 时记录 event_type="compression" 事件
6.ToolRunner 的 execute_tool 接收 trace：
   1) 开头记录 event_type="tool" 事件，detail 为 str(arguments)[:200]
   2) guard_tool_call 拦截时记录 event_type="blocked"，detail 为拦截错误信息
   3) guard_recent_tool_call 拦截时记录 event_type="blocked_recent"，detail 为拦截错误信息
7.以上各记录点按轮次串成一条可读的 Agent 运行轨迹，与 AgentMetrics 的数值汇总互补
```

## 15 Progress / Stagnation Detection 进度和停滞检测

### 15.1 将这些作为真正的Progress:
```
成功创建 Plan

Todo 状态发生变化

成功修改文件

workspace_revision 增加

成功完成验证

Todo 被标记 blocked，并给出原因

```

### 15.2 状态字段（AgentState）
```
1.last_progress_turn：最近一次真正取得进展发生在第几个 model turn，初始为 0
2.stagnation_warnings：当前连续停滞的告警计数，取得进展时清零
3.MAX_STAGNANT_TURNS = 5：允许的最大停滞轮数（planing.py 常量）
```

### 15.3 核心函数（planing.py）
```
1.record_progress(state, turn)
   - 记录进展：state.last_progress_turn = turn
   - 同时把 state.stagnation_warnings 重置为 0
   - 调用点：update_task 中 Todo 状态真正变化时；
     tool_runner / tools 中成功写入、完成验证等产生进展的动作处
2.is_stagnating(state, current_turn)
   - 还没有 plan（todos 为空）时直接返回 False
   - stagnant_turns = current_turn - state.last_progress_turn
   - 当 stagnant_turns >= MAX_STAGNANT_TURNS 时判定为停滞
3.build_stagnation_feedback(state)
   - 取出第一个 IN_PROGRESS 的 Todo 作为 current task（无法找到时用占位文本）
   - 返回 RUNTIME NOTICE，提示已停滞、给出当前任务，
     要求用已收集的信息采取下一步具体行动，不要继续无目的调研
```

### 15.4 与运行流程的集成（agent.run_agent）
```
1.每轮开始先调用 is_stagnating(state, current_turn=metrics.model_turns) 判断
2.判定停滞时：
   1) trace.record(event_type="stagnation", name="progress_guard",
      detail=f"last_progress_turn={state.last_progress_turn}")
   2) metrics.stagnation_warnings += 1
   3) build_stagnation_feedback 生成反馈，作为一条 user 消息追加进 history_turns
3.反馈以 user 消息形式注入下一轮上下文，驱动模型停止无效调研、回到具体行动
4.metrics.stagnation_warnings 纳入 AgentMetrics（agent_metrics.py），供运行汇总观测
```


## 16. Failure Recovery & Retry Policy。 故障恢复与重试策略

### 16.1 新增ToolResult，将工具调用execute_tool和其他工具调用返回的数据统一成ToolResult

新增 `tool_result.py`，定义统一的结果数据结构（`@dataclass ToolResult`）：
```
字段：
  success: bool              是否成功
  content: str = ""          正常输出内容
  error: str = ""            错误信息（失败时填充）
  return_code: int | None    命令返回码（可选）

构造方法：
  ToolResult.ok(content, return_code)   成功结果
  ToolResult.fail(error, content, return_code)  失败结果
```
`execute_tool` 及各类工具调用的返回值统一收敛为 `ToolResult`，下游只需判断 `result.success`，失败原因走 `result.error`。

### 16.2 新增failure_recovery，
```
1.FailureKind 错误类型类（str Enum）
   TRANSIENT 短暂错误 / NOT_FOUND 未找到 / INVALID_ARGUMENT 参数错误 /
   PERMISSION 权限错误 / CONFLICT 冲突 / TEST_FAILED 测试失败 /
   COMMAND_FAILED 命令失败 / UNKNOWN 未知
2.RecoveryAction 恢复的行为类（str Enum）
   RETRY 重试 / INSPECT 检查 / CHANGE_STRATEGY 变更策略 / BLOCKED 屏蔽
3.FailureDecision 失败的决策类
   kind + action + reason + auto_retry(默认False)
4.classify_failure 对错误进行分类
   先 raise 拦截 success 的结果；再对 error 文本做小写关键字匹配，
   依次判定 transient → permission → not_found → replace_text 冲突 →
   invalid_argument；最后按 tool_name=run_command 且 is_test_command 区分
   TEST_FAILED / COMMAND_FAILED；都不匹配则 UNKNOWN
5.decide_recovery 对各个类型的错误决定怎样恢复
   failure_count >= 3 → BLOCKED（同一错误反复出现，禁止再重复）
   TRANSIENT：安全工具(SAFE_AUTO_RETRY_TOOLS)且首次 → RETRY(auto_retry=True)，
              否则 CHANGE_STRATEGY
   NOT_FOUND / CONFLICT → INSPECT
   INVALID_ARGUMENT / TEST_FAILED / COMMAND_FAILED / UNKNOWN → CHANGE_STRATEGY
   PERMISSION → BLOCKED
6.make_failure_signature 构建错误签名
   f"{tool_name}:{failure_kind}:{sha1(arguments)[:12]}"
   例：read_file:not_found:a87f153922ab
7.record_failure 记录错误并返回错误个数
   签名与上次相同则 consecutive_count += 1，否则重置为 1
8.clear_failure_streak 清空错误个数，在tool调用成功后清空
   将 last_signature 置 None、consecutive_count 置 0
9.handle_tool_failure，处理错误，并返回决策和错误个数
   内部串联 classify_failure → make_failure_signature → record_failure →
   decide_recovery，返回 (decision, failure_count)
10.build_failure_result 构建失败原因
   在原始 error 后追加 RUNTIME RECOVERY 段（failure_kind / recovery_action /
   repeated_count / instruction），返回 ToolResult.fail(...)
11.execute_tool_with_recovery，故障恢复截止（位于 tool_runner.py）
   这是新增的包装函数，不是“把 run_agent 里的 execute_tool 改名”：
   execute_tool 仍然存在（tool_runner.py），execute_tool_with_recovery 在其
   之上叠加失败分类 / 计数 / 自动重试 / 结果构建；
   run_agent（agent.py）改为调用 execute_tool_with_recovery 来执行工具。
```

### 16.3 详细的故障恢复机制
```
入口：execute_tool_with_recovery(name, arguments, state, metrics, trace, current_turn)

1.第一次执行：metrics.tool_executions += 1，调用 execute_tool
2.成功：clear_failure_streak(state) 清空 failure，直接返回结果
3.第一次失败：
   1) metrics.tool_failures += 1
   2) handle_tool_failure → (decision, failure_count)
   3) trace.record(event_type="tool_failure", detail=kind/action/count/error前200字符)
4.是否自动重试：仅当 decision.auto_retry 为真（TRANSIENT + 安全工具 + 首次）
   1) metrics.auto_retries += 1
   2) trace.record(event_type="retry", detail="Runtime automatic retry")
   3) metrics.tool_executions += 1，再次 execute_tool
5.重试成功：metrics.recovered_failures += 1，clear_failure_streak，
            trace.record(event_type="recovered")，返回 retry_result
6.重试仍失败：metrics.tool_failures += 1，再次 handle_tool_failure 得到
            second_decision/second_count；second_count >= 2 时
            metrics.repeated_failures += 1；返回 build_failure_result
7.不自动重试（auto_retry 为假）：failure_count >= 2 时
            metrics.repeated_failures += 1；返回 build_failure_result
            （决策/签名/计数在 3 步已经算好，此处直接构建失败结果）
```

### 16.4 观测与状态
```
- state.failure（AgentState）保存 last_signature 与 consecutive_count，
  用于跨轮次的“同一错误连续出现”判定
- 涉及指标（AgentMetrics）：
  tool_executions / tool_failures / auto_retries /
  recovered_failures / repeated_failures
- 涉及 trace 事件：tool_failure / retry / recovered
- 失败结果以 ToolResult.fail 返回，附带 RUNTIME RECOVERY 文本，
  作为工具输出注入模型上下文，驱动其检查、改参或换策略
```


## 17. Verification / Task Completion Protocol 验证/任务完成协议

## 17.1 任务完成不在只靠状态变成COMPLETION，而是看是否测试完成，要有证据证明完成了
```
          Task Completion
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
    Plan       Work     Verification
   Complete    Done        Passed
```
