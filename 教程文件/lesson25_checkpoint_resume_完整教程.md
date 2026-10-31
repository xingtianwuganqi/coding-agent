# 第 25 课：Checkpoint / Resume / State Persistence（完整教程）

> 代码基线：上传的 `mini-coding-agent(4).zip`。本课是教学版、保守恢复机制。所有代码和补丁均在文末或 ZIP 中。

## 本课目标

1. 将运行中的 `AgentState`、`ProgressTracker`、`AgentMetrics`、`AgentTrace` 和 `history_turns` 安全持久化到 JSON。
2. 加入 CLI 的 `--checkpoint` / `--resume`，恢复进度与模型对话历史。
3. 将单个工具调用区分为“即将执行、已记录结果、整轮已提交”三种状态。
4. 对执行结果不确定的工具、外部文件变动、损坏和不同版本的 Checkpoint 采取**拒绝自动恢复**策略。
5. 使用单元测试、可控的模拟模型集成测试证明行为。

## 你项目的实际源码调用关系

```text
mini-coding-agent CLI
  └── cli.py -> run_agent(task, checkpoint_path, resume)
       ├── checkpoint.py / CheckpointStore.load() 或 RunSession() 新建
       ├── runtime_lifecycle.py / ProgressTracker.observe(), check_lifecycle()
       ├── context.py / compress_history(), build_model_input()
       ├── model_api.py / call_model()
       ├── CheckpointStore.begin_turn()                  [模型已返回 Tool Calls]
       ├── for call in tool_calls:
       │    ├── CheckpointStore.before_tool()           [先落盘 pending 标记]
       │    ├── guard_tool_call + execute_tool_with_recovery()
       │    └── CheckpointStore.after_tool()            [落盘 ToolResult + AgentState]
       ├── CheckpointStore.finish_turn()                [形成完整 history_turn]
       ├── ProgressTracker.observe(); check_lifecycle()
       └── finish_agent() -> save + metrics + final outcome
```

## 一、为什么不是只存 Plan？

`AgentState` 有 `workspace_revision`、`code_revision`、`changed_files`、验证结果及版本化 Evidence。单独恢复 Plan 可能错误保留已经失效的 `tests_passed`。此外恢复还必须保留累计轮次，防止每次重启都获得一个新的模型预算。

这次设计一个 `RunSession` 统一管理：

```python
@dataclass
class RunSession:
    state: AgentState
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    trace: AgentTrace = field(default_factory=AgentTrace)
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    history_turns: list[list[dict]] = field(default_factory=list)
    inflight_turn: list[dict] | None = None
    pending_tool: dict | None = None
```

- `history_turns` 是已经完整提交、能够直接送给模型的历史。
- `inflight_turn` 保存模型返回、但还没有完全处理的一批 Tool Calls。
- `pending_tool` 表示某个工具开始执行前已记录准备标记，但完成结果还没安全入盘。

## 二、序列化为何选白名单 JSON 而不是 pickle？

`AgentState` 含 dataclass、Enum、set、以 Enum 为键的 dict，以及嵌套历史。原样 `json.dumps(state)` 不可行。参考实现 `_encode/_decode` 对上述数据进行显式编码，解码只允许代码中预定义的类型白名单。

不使用 pickle，是因为 pickle 反序列化来自不可信文件的数据可能执行任意 Python 代码。此方案仍只是教学版，未来添加/删除字段需要迁移 Schema，不能只修改 Dataclass 就指望旧文件永久兼容。

## 三、原子保存步骤

```text
写入 checkpoint.json.tmp
       ↓
flush + os.fsync(文件)
       ↓
os.replace(tmp, checkpoint.json)
       ↓
fsync(父目录，POSIX)
```

`os.replace` 使文件更替在同一文件系统中具备原子性。Checksum 用于检测意外损坏，但**不是抗恶意篡改的签名**。存储包含聊天和工具参数，应放在工作区外并保护权限。

## 四、工具执行前后分别保存

```python
checkpoint_store.begin_turn(session, turn_items[0])

for call in tool_calls:
    checkpoint_store.before_tool(session, call.id, call.function.name, {})

    # 旧的 Guard -> execute_tool_with_recovery() -> ToolResult 链保持原样
    tool_output = {
        "role": "tool",
        "tool_call_id": call.id,
        "content": result.content if result.success else result.error,
    }

    checkpoint_store.after_tool(session, tool_output)

checkpoint_store.finish_turn(session)
```

此示例展示接入点，实际补丁保留了 `agent.py` 的异常处理、metrics、permission 和 tool guard。工具被拒绝也要形成 Tool Response，保持模型协议上的 tool_call_id 对应关系。

### 崩溃点与结果

| 崩溃点 | 磁盘状态 | 恢复策略 |
|---|---|---|
| 调用模型之前 | 完整 `history_turns`，无 pending | 自动恢复 |
| 模型返回后、工具开始前 | `inflight_turn` 存在 | 暂停，需要人工核对，避免错误继续 |
| 工具执行中 | `pending_tool` 存在 | 绝不自动重放 |
| 工具成功、保存结果之前 | `pending_tool` 存在 | 绝不自动重放 |
| 完整 Tool Batch 已保存 | 无 pending、无 inflight | 自动恢复 |
| 恢复前有人修改工作区 | Fingerprint 不一致 | 暂停，需要检查源码和证据 |

这是 **at-most-once automatic replay** 的保守策略，并没有实现分布式事务或真正的 exactly-once execution。用户可以检查冲突后新建修复任务，但不能在不知道副作用时直接强制自动重试。

## 五、在 `agent.py` 中的具体位置

1. 新增 `run_agent(task, *, checkpoint_path=None, resume=False)`，`resume=True` 时从 `CheckpointStore.load()` 拿回 `session`，否则创建新 `RunSession`。
2. 将 `state = session.state`、`progress = session.progress`、`history_turns = session.history_turns`，尤其不要在下面再写 `history_turns=[]`。
3. 每次 Model Turn 增加后保存一次，以保留预算；`compress_history` 修改 summary/history 后也保存。
4. 模型有 Tool Calls 时，先 `begin_turn`；循环里每个工具调用前 `before_tool`，调用后 `after_tool`；整批完成 `finish_turn`。
5. 无工具调用时若添加了 Completion Feedback，也要保存 `history_turns`。
6. `ProgressTracker.observe()` 后保存，使下一次恢复沿用进展里程碑和停滞时长。
7. `finish_agent()` 统一记录最终指标并保存，不重新调用模型做总结。

## 六、CLI 操作

```bash
# 创建任务：建议使用工作区外的私有持久化路径
uv run mini-coding-agent --checkpoint "$HOME/.mini-agent-checkpoints/refactor-001.json"

# 意外中断后：从同一个 checkpoint 恢复
uv run mini-coding-agent --checkpoint "$HOME/.mini-agent-checkpoints/refactor-001.json" --resume

# 测试
uv run pytest -q
```

一个 checkpoint 文件对应一个任务。已有文件不会被一个新任务覆盖。如果收到 `RecoveryRequired`，请检查日志、git diff 与文件系统，**不要**在没有核对的情况下删除 Checkpoint 然后重跑同一个修改任务。

## 七、本课完整新增源码：`checkpoint.py`

```python
"""Lesson 25: safe, versioned JSON checkpoints for one coding-agent run.

A checkpoint is an execution cursor, NOT a filesystem backup.
An uncertain in-flight tool call must never be replayed automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .agent_metrics import AgentMetrics
from .agent_trace import AgentTrace, TraceEvent
from .evidence import EvidenceType
from .goal import GoalState
from .planning import (
    AgentState, FailureState, PlanSnapshot, PlanState, PlanStatus,
    TaskStatus, TodoItem, TodoKind, VerificationState, ReplanReason,
)
from .requirements import RequirementState, RequirementKind, TaskRequirement
from .runtime_lifecycle import ProgressTracker
from .verification import VerificationKind, VerificationRecord

SCHEMA_VERSION = 1
SKIP_DIRS = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'node_modules', '.mini_agent',
    'dist', 'build',
}
SKIP_FILES = {'.DS_Store'}
MAX_FINGERPRINT_FILES = 10000

_TYPES = {t.__name__: t for t in (
    AgentState, GoalState, PlanState, PlanSnapshot, FailureState,
    VerificationState, TodoItem, TaskRequirement, RequirementState,
    VerificationRecord, AgentMetrics, AgentTrace, TraceEvent, ProgressTracker,
)}
_ENUMS = {t.__name__: t for t in (
    EvidenceType, PlanStatus, TaskStatus, TodoKind, ReplanReason,
    RequirementKind, VerificationKind,
)}


class CheckpointError(RuntimeError):
    """Unusable or incompatible checkpoint; do not silently start over."""


class RecoveryRequired(CheckpointError):
    """A tool may have executed without its result being committed."""


@dataclass
class RunSession:
    state: AgentState
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    trace: AgentTrace = field(default_factory=AgentTrace)
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    history_turns: list[list[dict[str, Any]]] = field(default_factory=list)
    # Assistant message plus tool responses not yet moved to history_turns.
    inflight_turn: list[dict[str, Any]] | None = None
    # A tool is marked pending BEFORE it is invoked, then cleared AFTER its
    # ToolResult and AgentState have both been saved.
    pending_tool: dict[str, Any] | None = None


_TYPES['RunSession'] = RunSession


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {'$enum': type(value).__name__, '$value': value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            '$dataclass': type(value).__name__,
            '$fields': {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, set):
        return {'$set': [_encode(v) for v in sorted(value, key=str)]}
    if isinstance(value, tuple):
        return {'$tuple': [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: _encode(v) for k, v in value.items()}
        return {'$pairs': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f'Cannot checkpoint {type(value).__name__}')


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'$enum', '$value'}:
        enum_cls = _ENUMS.get(value['$enum'])
        if enum_cls is None:
            raise CheckpointError(f'Unknown enum: {value["$enum"]}')
        return enum_cls(value['$value'])
    if set(value) == {'$dataclass', '$fields'}:
        cls = _TYPES.get(value['$dataclass'])
        if cls is None:
            raise CheckpointError(f'Unknown state type: {value["$dataclass"]}')
        return cls(**{k: _decode(v) for k, v in value['$fields'].items()})
    if set(value) == {'$set'}:
        return set(_decode(v) for v in value['$set'])
    if set(value) == {'$tuple'}:
        return tuple(_decode(v) for v in value['$tuple'])
    if set(value) == {'$pairs'}:
        return {_decode(k): _decode(v) for k, v in value['$pairs']}
    return {k: _decode(v) for k, v in value.items()}


def workspace_fingerprint(root: Path) -> str:
    """Hash current non-ignored workspace files, excluding checkpoints.

    Large binary files are read in chunks; this is intentionally conservative
    for the learning project, not optimized for giant monorepos.
    """
    root = root.resolve()
    digest = hashlib.sha256()
    count = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not (Path(directory) / d).is_symlink())
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if filename in SKIP_FILES or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith('.mini_agent/'):
                continue
            count += 1
            if count > MAX_FINGERPRINT_FILES:
                raise CheckpointError('Too many workspace files for fingerprint')
            digest.update(relative.encode('utf-8'))
            digest.update(b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


class CheckpointStore:
    def __init__(self, path: Path, workspace: Path):
        self.path = Path(path).resolve()
        self.workspace = Path(workspace).resolve()

    def save(self, session: RunSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            'schema': SCHEMA_VERSION,
            'workspace': str(self.workspace),
            'workspace_fingerprint': workspace_fingerprint(self.workspace),
            'session': _encode(session),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        envelope = {'sha256': hashlib.sha256(body).hexdigest(), 'payload': payload}
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                             indent=2, allow_nan=False).encode('utf-8')
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            # Sync the renamed directory entry too (works on POSIX).
            if os.name == 'posix':
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if tmp.exists():
                tmp.unlink()

    def load(self) -> RunSession:
        if not self.path.is_file():
            raise CheckpointError(f'Checkpoint not found: {self.path}')
        try:
            envelope = json.loads(self.path.read_text(encoding='utf-8'))
            payload = envelope['payload']
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False).encode('utf-8')
            if hashlib.sha256(body).hexdigest() != envelope['sha256']:
                raise CheckpointError('Checkpoint checksum mismatch')
            if payload['schema'] != SCHEMA_VERSION:
                raise CheckpointError('Unsupported checkpoint schema')
            if payload['workspace'] != str(self.workspace):
                raise CheckpointError('Checkpoint belongs to another workspace')
            session = _decode(payload['session'])
            if not isinstance(session, RunSession):
                raise CheckpointError('Checkpoint has invalid session data')
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CheckpointError(f'Invalid checkpoint: {exc}') from exc
        if session.pending_tool is not None:
            tool = session.pending_tool
            raise RecoveryRequired(
                f'UNCERTAIN tool {tool.get("name")} '
                f'call_id={tool.get("call_id")}; do not auto-replay. '
                'Inspect workspace and reconcile manually.'
            )
        if session.inflight_turn is not None:
            raise RecoveryRequired(
                'Partial model tool-call turn exists. '
                'Do not auto-replay missing tool calls.'
            )
        current = workspace_fingerprint(self.workspace)
        if current != payload['workspace_fingerprint']:
            raise RecoveryRequired(
                'Workspace changed since checkpoint. '
                'Inspect changes before restoring state or evidence.'
            )
        return session

    def begin_turn(self, session: RunSession, assistant: dict[str, Any]) -> None:
        if session.inflight_turn is not None:
            raise CheckpointError('Cannot begin turn with unfinished tool messages')
        session.inflight_turn = [assistant]
        self.save(session)

    def before_tool(self, session: RunSession, call_id: str,
                    name: str, arguments: dict[str, Any]) -> None:
        if session.inflight_turn is None or session.pending_tool is not None:
            raise CheckpointError('Invalid pending tool transition')
        # Do not store arguments twice; assistant message already contains them.
        session.pending_tool = {'call_id': call_id, 'name': name}
        self.save(session)

    def after_tool(self, session: RunSession, tool_output: dict[str, Any]) -> None:
        if session.pending_tool is None or session.inflight_turn is None:
            raise CheckpointError('No pending tool to commit')
        if tool_output.get('tool_call_id') != session.pending_tool['call_id']:
            raise CheckpointError('Tool response ID does not match pending call')
        session.inflight_turn.append(tool_output)
        session.pending_tool = None
        self.save(session)

    def finish_turn(self, session: RunSession) -> None:
        if session.pending_tool is not None or session.inflight_turn is None:
            raise CheckpointError('Cannot commit incomplete turn')
        session.history_turns.append(session.inflight_turn)
        session.inflight_turn = None
        self.save(session)
```

## 八、对现有 `agent.py` 和 `cli.py` 的完整增量补丁

> 此补丁只针对上传的 `(4).zip` 版本；已有本地改动请先 `git apply --check`。

```diff
--- a/src/mini_coding_agent/agent.py
+++ b/src/mini_coding_agent/agent.py
@@ -2,6 +2,9 @@
 
 import json
 import os
+from pathlib import Path
+from .config import WORKSPACE
+from .checkpoint import CheckpointStore, CheckpointError, RunSession
 
 from .config import MAX_MODEL_TURN
 from .model_api import ModelCallError, call_model
@@ -70,7 +73,9 @@
         metrics: AgentMetrics,
         trace: AgentTrace,
         decision: LifecycleDecision,
-        preferred_text: str = ""
+        preferred_text: str = "",
+        session: RunSession | None = None,
+        checkpoint_store: CheckpointStore | None = None,
 ) -> str:
     """
     统一的单词终止出口：无需模型继续决定是否停止。
@@ -84,11 +89,13 @@
         detail=metrics.stop_reaseon or "requirements_satisfied",
     )
     metrics.end_runing()
+    if checkpoint_store is not None and session is not None:
+        checkpoint_store.save(session)
     trace.print_trace()
 
     # 已通过 Runtime 检查，才允许返回模型此前的总结文字
     if decision.outcome == RunOutcome.COMPLETE:
-        return preferred_text.strip()
+        return preferred_text.strip() or "Outcome: complete\nAll completion requirements satisfied."
 
     missing = [f"- {r.name}: {r.reason}" for r in decision.report.missing]
     changed = sorted(state.changed_files)
@@ -103,41 +110,61 @@
     return "\n".join(lines)
 
 
-def run_agent(task: str) -> str:
-
-    state = create_agent_state(
-        user_prompt=task
+def run_agent(
+    task: str,
+    *,
+    checkpoint_path: str | None = None,
+    resume: bool = False,
+) -> str:
+    """Run agent, optionally persisting a safe checkpoint after each tool.
+
+    Resume NEVER auto-replays an uncertain tool call. Use a separate
+    checkpoint path for each independent task.
+    """
+    if resume and not checkpoint_path:
+        raise CheckpointError("--resume requires --checkpoint")
+
+    checkpoint_store = (
+        CheckpointStore(Path(checkpoint_path), WORKSPACE)
+        if checkpoint_path else None
     )
-
-    # 指标对象
-    metrics = AgentMetrics()
-
-    # 追踪
-    trace = AgentTrace()
-
-    # 24课单独追踪“真实里程碑”，不依赖旧的 record_progress()。
+    if resume:
+        assert checkpoint_store is not None
+        session = checkpoint_store.load()
+        saved_task = session.state.goal.original_request
+        if task and task != saved_task:
+            raise CheckpointError("Task does not match saved checkpoint")
+        task = saved_task
+    else:
+        if checkpoint_store is not None and checkpoint_store.path.exists():
+            raise CheckpointError(
+                "Checkpoint already exists. Resume it or use a new path; "
+                "do not overwrite an unfinished run."
+            )
+        session = RunSession(state=create_agent_state(user_prompt=task))
+        if checkpoint_store is not None:
+            checkpoint_store.save(session)
+
+    state = session.state
+    metrics = session.metrics
+    trace = session.trace
+    progress = session.progress
+    history_turns = session.history_turns
+
     budget = RuntimeBudget(
         max_model_turns=min(MAX_MODEL_TURN, 120),
         max_no_progress_turns=30,
         warn_no_progress_turns=12,
     )
-    progress = ProgressTracker()
     progress.observe(
         state=state,
-        report=check_lifecycle(state, 0, progress, budget).report,
-        turn=0
+        report=check_lifecycle(state, metrics.model_turns, progress, budget).report,
+        turn=metrics.model_turns,
     )
-
-    # allow_blocked_final = False
-    
-    # input_items = [
-    #     {
-    #         "role": "user",
-    #         "content": task,
-    #     }
-    # ]
-
-    history_turns : list[list] = []
+    if checkpoint_store is not None:
+        checkpoint_store.save(session)
+
+    # history_turns points to the restored session; never reset on resume.
 
     while True:
         # 模型调用之前先判断：已完成就不要继续消耗 Token。
@@ -147,7 +174,9 @@
                 state=state,
                 metrics=metrics,
                 trace=trace,
-                decision=decision
+                decision=decision,
+                session=session,
+                checkpoint_store=checkpoint_store,
             )
         # 仅警告一次；达到硬阈值则由上面的Controller 直接终止。
         if progress.should_warn(metrics.model_turns, budget):
@@ -158,6 +187,8 @@
                 "content": build_stagnation_feedback(state)
             }])
         metrics.model_turns += 1
+        if checkpoint_store is not None:
+            checkpoint_store.save(session)
         turn = metrics.model_turns
         print(f"\n --- Agent step {turn} ---")
         trace.record(
@@ -222,9 +253,14 @@
                 state=state,
                 metrics=metrics,
                 trace=trace,
-                decision=decision
+                decision=decision,
+                session=session,
+                checkpoint_store=checkpoint_store,
             )
             return f"Agent stopped: {error}"
+
+        if checkpoint_store is not None:
+            checkpoint_store.save(session)  # compress_history may change summary/history
 
         print_fixed_context_breakdown(
             system_prompt=SYSTEM_PROMPT,
@@ -298,7 +334,8 @@
                 outcome=RunOutcome.STOPPED,
                 reason=StopReason.MODEL_ERROR,
             )
-            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)
+            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision,
+                session=session, checkpoint_store=checkpoint_store)
             return f"Agent stopped: {error}"
         # Preserve reasoning_content alongside tool calls for GLM's next turn.
         # input_items.append(message.model_dump(mode="json", exclude_none=True))
@@ -326,7 +363,8 @@
             if decision.action != LifecycleAction.CONTINUE:
                 return finish_agent(
                     state=state, metrics=metrics, trace=trace,
-                    decision=decision, preferred_text=message.content
+                    decision=decision, preferred_text=message.content,
+                    session=session, checkpoint_store=checkpoint_store
                 )
 
             metrics.completion_rejections += 1
@@ -334,6 +372,8 @@
                 "role": "user",
                 "content": build_completion_feedback(decision.report, state),
             }])
+            if checkpoint_store is not None:
+                checkpoint_store.save(session)
             continue
 
         # v2
@@ -476,6 +516,9 @@
         # -------------------------
         # 5. 执行 Tool Calls
         # -------------------------
+        if checkpoint_store is not None:
+            checkpoint_store.begin_turn(session, turn_items[0])
+
         for call in tool_calls:
             print(f"Tool: {call.function.name}")
             print(
@@ -486,6 +529,10 @@
                 )
             )
             
+            # Durable write-ahead marker BEFORE any tool side effect.
+            if checkpoint_store is not None:
+                checkpoint_store.before_tool(session, call.id, call.function.name, {})
+
             try:
                 name = call.function.name
                 arguments = json.loads(call.function.arguments)
@@ -585,12 +632,15 @@
             turn_items.append(
                 tool_output
             )
+            if checkpoint_store is not None:
+                checkpoint_store.after_tool(session, tool_output)
         # -------------------------
         # 6. 保存完整的这一轮
         # -------------------------
-        history_turns.append(
-            turn_items
-        )
+        if checkpoint_store is not None:
+            checkpoint_store.finish_turn(session)
+        else:
+            history_turns.append(turn_items)
 
         # 一批工具全部执行、Tool Response 都保存以后，再统一检查
         # 由实际状态和有效证据判断进展，set_plan/replan 本身不自动算进展
@@ -619,13 +669,14 @@
             budget=budget
         )
         metrics.completion_checks += 1
+        if checkpoint_store is not None:
+            checkpoint_store.save(session)  # persist ProgressTracker milestones
         if decision.action != LifecycleAction.CONTINUE:
             return finish_agent(
                 state=state,
                 metrics=metrics,
                 trace=trace,
-                decision=decision
-            )
-
-
-
+                decision=decision,
+                session=session,
+                checkpoint_store=checkpoint_store,
+            )
--- a/src/mini_coding_agent/cli.py
+++ b/src/mini_coding_agent/cli.py
@@ -1,6 +1,7 @@
 """Command line entrypoint for the mini coding agent."""
 
 from .agent import run_agent
+import argparse
 
 
 def read_task() -> str:
@@ -19,6 +20,17 @@
     return "\n".join(lines).strip()
 
 def main():
+    parser = argparse.ArgumentParser(description="Mini Coding Agent")
+    parser.add_argument("--checkpoint", help="Checkpoint JSON path for this task")
+    parser.add_argument("--resume", action="store_true", help="Resume saved safe checkpoint")
+    args = parser.parse_args()
+    if args.resume:
+        if not args.checkpoint:
+            parser.error("--resume requires --checkpoint")
+        result = run_agent("", checkpoint_path=args.checkpoint, resume=True)
+        print("\nAgent >\n" + result)
+        return
+
     print("Mini Coding Agent")
     print("输入 :send 发送任务")
     print("输入 exit 后再输入 :send 退出\n")
@@ -32,7 +44,7 @@
         if not task:
             continue
 
-        result = run_agent(task)
+        result = run_agent(task, checkpoint_path=args.checkpoint)
 
         print("\nAgent >")
         print(result)
--- /dev/null
+++ b/src/mini_coding_agent/checkpoint.py
@@ -0,0 +1,255 @@
+"""Lesson 25: safe, versioned JSON checkpoints for one coding-agent run.
+
+A checkpoint is an execution cursor, NOT a filesystem backup.
+An uncertain in-flight tool call must never be replayed automatically.
+"""
+from __future__ import annotations
+
+import hashlib
+import json
+import os
+from dataclasses import dataclass, field, fields, is_dataclass
+from enum import Enum
+from pathlib import Path
+from typing import Any
+
+from .agent_metrics import AgentMetrics
+from .agent_trace import AgentTrace, TraceEvent
+from .evidence import EvidenceType
+from .goal import GoalState
+from .planning import (
+    AgentState, FailureState, PlanSnapshot, PlanState, PlanStatus,
+    TaskStatus, TodoItem, TodoKind, VerificationState, ReplanReason,
+)
+from .requirements import RequirementState, RequirementKind, TaskRequirement
+from .runtime_lifecycle import ProgressTracker
+from .verification import VerificationKind, VerificationRecord
+
+SCHEMA_VERSION = 1
+SKIP_DIRS = {
+    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
+    '.mypy_cache', '.ruff_cache', 'node_modules', '.mini_agent',
+    'dist', 'build',
+}
+SKIP_FILES = {'.DS_Store'}
+MAX_FINGERPRINT_FILES = 10000
+
+_TYPES = {t.__name__: t for t in (
+    AgentState, GoalState, PlanState, PlanSnapshot, FailureState,
+    VerificationState, TodoItem, TaskRequirement, RequirementState,
+    VerificationRecord, AgentMetrics, AgentTrace, TraceEvent, ProgressTracker,
+)}
+_ENUMS = {t.__name__: t for t in (
+    EvidenceType, PlanStatus, TaskStatus, TodoKind, ReplanReason,
+    RequirementKind, VerificationKind,
+)}
+
+
+class CheckpointError(RuntimeError):
+    """Unusable or incompatible checkpoint; do not silently start over."""
+
+
+class RecoveryRequired(CheckpointError):
+    """A tool may have executed without its result being committed."""
+
+
+@dataclass
+class RunSession:
+    state: AgentState
+    metrics: AgentMetrics = field(default_factory=AgentMetrics)
+    trace: AgentTrace = field(default_factory=AgentTrace)
+    progress: ProgressTracker = field(default_factory=ProgressTracker)
+    history_turns: list[list[dict[str, Any]]] = field(default_factory=list)
+    # Assistant message plus tool responses not yet moved to history_turns.
+    inflight_turn: list[dict[str, Any]] | None = None
+    # A tool is marked pending BEFORE it is invoked, then cleared AFTER its
+    # ToolResult and AgentState have both been saved.
+    pending_tool: dict[str, Any] | None = None
+
+
+_TYPES['RunSession'] = RunSession
+
+
+def _encode(value: Any) -> Any:
+    if isinstance(value, Enum):
+        return {'$enum': type(value).__name__, '$value': value.value}
+    if is_dataclass(value) and not isinstance(value, type):
+        return {
+            '$dataclass': type(value).__name__,
+            '$fields': {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
+        }
+    if isinstance(value, set):
+        return {'$set': [_encode(v) for v in sorted(value, key=str)]}
+    if isinstance(value, tuple):
+        return {'$tuple': [_encode(v) for v in value]}
+    if isinstance(value, list):
+        return [_encode(v) for v in value]
+    if isinstance(value, dict):
+        if all(isinstance(k, str) for k in value):
+            return {k: _encode(v) for k, v in value.items()}
+        return {'$pairs': [[_encode(k), _encode(v)] for k, v in value.items()]}
+    if value is None or isinstance(value, (str, bool, int, float)):
+        return value
+    raise TypeError(f'Cannot checkpoint {type(value).__name__}')
+
+
+def _decode(value: Any) -> Any:
+    if isinstance(value, list):
+        return [_decode(v) for v in value]
+    if not isinstance(value, dict):
+        return value
+    if set(value) == {'$enum', '$value'}:
+        enum_cls = _ENUMS.get(value['$enum'])
+        if enum_cls is None:
+            raise CheckpointError(f'Unknown enum: {value["$enum"]}')
+        return enum_cls(value['$value'])
+    if set(value) == {'$dataclass', '$fields'}:
+        cls = _TYPES.get(value['$dataclass'])
+        if cls is None:
+            raise CheckpointError(f'Unknown state type: {value["$dataclass"]}')
+        return cls(**{k: _decode(v) for k, v in value['$fields'].items()})
+    if set(value) == {'$set'}:
+        return set(_decode(v) for v in value['$set'])
+    if set(value) == {'$tuple'}:
+        return tuple(_decode(v) for v in value['$tuple'])
+    if set(value) == {'$pairs'}:
+        return {_decode(k): _decode(v) for k, v in value['$pairs']}
+    return {k: _decode(v) for k, v in value.items()}
+
+
+def workspace_fingerprint(root: Path) -> str:
+    """Hash current non-ignored workspace files, excluding checkpoints.
+
+    Large binary files are read in chunks; this is intentionally conservative
+    for the learning project, not optimized for giant monorepos.
+    """
+    root = root.resolve()
+    digest = hashlib.sha256()
+    count = 0
+    for directory, dirnames, filenames in os.walk(root, followlinks=False):
+        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
+                             and not (Path(directory) / d).is_symlink())
+        for filename in sorted(filenames):
+            path = Path(directory) / filename
+            if filename in SKIP_FILES or path.is_symlink():
+                continue
+            relative = path.relative_to(root).as_posix()
+            if relative.startswith('.mini_agent/'):
+                continue
+            count += 1
+            if count > MAX_FINGERPRINT_FILES:
+                raise CheckpointError('Too many workspace files for fingerprint')
+            digest.update(relative.encode('utf-8'))
+            digest.update(b'\0')
+            with path.open('rb') as stream:
+                while chunk := stream.read(1024 * 1024):
+                    digest.update(chunk)
+            digest.update(b'\0')
+    return digest.hexdigest()
+
+
+class CheckpointStore:
+    def __init__(self, path: Path, workspace: Path):
+        self.path = Path(path).resolve()
+        self.workspace = Path(workspace).resolve()
+
+    def save(self, session: RunSession) -> None:
+        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
+        payload = {
+            'schema': SCHEMA_VERSION,
+            'workspace': str(self.workspace),
+            'workspace_fingerprint': workspace_fingerprint(self.workspace),
+            'session': _encode(session),
+        }
+        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
+                          separators=(',', ':'), allow_nan=False).encode('utf-8')
+        envelope = {'sha256': hashlib.sha256(body).hexdigest(), 'payload': payload}
+        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
+                             indent=2, allow_nan=False).encode('utf-8')
+        tmp = self.path.with_name(self.path.name + '.tmp')
+        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
+        try:
+            with os.fdopen(fd, 'wb') as stream:
+                stream.write(content)
+                stream.flush()
+                os.fsync(stream.fileno())
+            os.replace(tmp, self.path)
+            # Sync the renamed directory entry too (works on POSIX).
+            if os.name == 'posix':
+                dir_fd = os.open(self.path.parent, os.O_RDONLY)
+                try:
+                    os.fsync(dir_fd)
+                finally:
+                    os.close(dir_fd)
+        finally:
+            if tmp.exists():
+                tmp.unlink()
+
+    def load(self) -> RunSession:
+        if not self.path.is_file():
+            raise CheckpointError(f'Checkpoint not found: {self.path}')
+        try:
+            envelope = json.loads(self.path.read_text(encoding='utf-8'))
+            payload = envelope['payload']
+            body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
+                              separators=(',', ':'), allow_nan=False).encode('utf-8')
+            if hashlib.sha256(body).hexdigest() != envelope['sha256']:
+                raise CheckpointError('Checkpoint checksum mismatch')
+            if payload['schema'] != SCHEMA_VERSION:
+                raise CheckpointError('Unsupported checkpoint schema')
+            if payload['workspace'] != str(self.workspace):
+                raise CheckpointError('Checkpoint belongs to another workspace')
+            session = _decode(payload['session'])
+            if not isinstance(session, RunSession):
+                raise CheckpointError('Checkpoint has invalid session data')
+        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
+            raise CheckpointError(f'Invalid checkpoint: {exc}') from exc
+        if session.pending_tool is not None:
+            tool = session.pending_tool
+            raise RecoveryRequired(
+                f'UNCERTAIN tool {tool.get("name")} '
+                f'call_id={tool.get("call_id")}; do not auto-replay. '
+                'Inspect workspace and reconcile manually.'
+            )
+        if session.inflight_turn is not None:
+            raise RecoveryRequired(
+                'Partial model tool-call turn exists. '
+                'Do not auto-replay missing tool calls.'
+            )
+        current = workspace_fingerprint(self.workspace)
+        if current != payload['workspace_fingerprint']:
+            raise RecoveryRequired(
+                'Workspace changed since checkpoint. '
+                'Inspect changes before restoring state or evidence.'
+            )
+        return session
+
+    def begin_turn(self, session: RunSession, assistant: dict[str, Any]) -> None:
+        if session.inflight_turn is not None:
+            raise CheckpointError('Cannot begin turn with unfinished tool messages')
+        session.inflight_turn = [assistant]
+        self.save(session)
+
+    def before_tool(self, session: RunSession, call_id: str,
+                    name: str, arguments: dict[str, Any]) -> None:
+        if session.inflight_turn is None or session.pending_tool is not None:
+            raise CheckpointError('Invalid pending tool transition')
+        # Do not store arguments twice; assistant message already contains them.
+        session.pending_tool = {'call_id': call_id, 'name': name}
+        self.save(session)
+
+    def after_tool(self, session: RunSession, tool_output: dict[str, Any]) -> None:
+        if session.pending_tool is None or session.inflight_turn is None:
+            raise CheckpointError('No pending tool to commit')
+        if tool_output.get('tool_call_id') != session.pending_tool['call_id']:
+            raise CheckpointError('Tool response ID does not match pending call')
+        session.inflight_turn.append(tool_output)
+        session.pending_tool = None
+        self.save(session)
+
+    def finish_turn(self, session: RunSession) -> None:
+        if session.pending_tool is not None or session.inflight_turn is None:
+            raise CheckpointError('Cannot commit incomplete turn')
+        session.history_turns.append(session.inflight_turn)
+        session.inflight_turn = None
+        self.save(session)
--- /dev/null
+++ b/tests/test_checkpoint.py
@@ -0,0 +1,217 @@
+"""Lesson 25 checkpoint tests: no live model calls."""
+from pathlib import Path
+
+import pytest
+
+from mini_coding_agent.agent_metrics import AgentMetrics
+from mini_coding_agent.checkpoint import (
+    CheckpointError, CheckpointStore, RecoveryRequired, RunSession,
+)
+from mini_coding_agent.evidence import EvidenceType
+from mini_coding_agent.planning import (
+    PlanSnapshot, PlanStatus, TaskStatus, TodoItem, TodoKind,
+    create_agent_state,
+)
+from mini_coding_agent.requirements import RequirementKind, TaskRequirement
+from mini_coding_agent.runtime_lifecycle import ProgressTracker
+from mini_coding_agent.verification import VerificationKind, VerificationRecord
+
+
+def setup_session(tmp_path: Path):
+    root = tmp_path / 'workspace'
+    root.mkdir()
+    (root / 'module.py').write_text('print(1)\n', encoding='utf-8')
+    store = CheckpointStore(root / '.mini_agent' / 'checkpoint.json', root)
+    session = RunSession(state=create_agent_state('refactor module'))
+    return root, store, session
+
+
+def test_full_state_roundtrip(tmp_path):
+    root, store, session = setup_session(tmp_path)
+    state = session.state
+    state.goal.locked = True
+    state.goal.objective = 'refactor module'
+    state.requirements.locked = True
+    state.requirements.items = [
+        TaskRequirement(id=1, kind=RequirementKind.MUST_CHANGE,
+                        description='update module', target='module.py')
+    ]
+    state.plan.revision = 2
+    state.plan.replan_count = 1
+    state.plan.items = [
+        TodoItem(id=1, content='Change', status=TaskStatus.COMPLETED,
+                 kind=TodoKind.IMPLEMENTATION, requirement_ids=[1]),
+        TodoItem(id=2, content='Test', depends_on=[1],
+                 required_evidence=EvidenceType.TESTS_PASSED,
+                 reuse_from_task_id=3),
+    ]
+    state.plan.history = [
+        PlanSnapshot(revision=1, items=[TodoItem(id=3, content='Old')],
+                     status=PlanStatus.SUPERSEDED, reason='blocked',
+                     created_turn=4, superseded_turn=7)
+    ]
+    state.workspace_revision = 5
+    state.code_revision = 3
+    state.changed_files = {'module.py'}
+    state.evidence = {EvidenceType.TESTS_PASSED: 5}
+    state.verification.records = [
+        VerificationRecord(VerificationKind.TEST, True, 5, 3,
+                           'pytest', 'all passed')
+    ]
+    session.progress = ProgressTracker(last_meaningful_turn=7,
+                                       seen_milestones={'plan_created'})
+    session.metrics = AgentMetrics(model_turns=8, tool_calls=10)
+    session.history_turns = [[{'role': 'user', 'content': 'earlier check'}]]
+    session.trace.record(7, 'tool', 'pytest', 'passed')
+    store.save(session)
+    loaded = store.load()
+    assert loaded.state.goal.objective == 'refactor module'
+    assert loaded.state.plan.history[0].items[0].id == 3
+    assert loaded.state.plan.items[1].depends_on == [1]
+    assert loaded.state.plan.items[1].reuse_from_task_id == 3
+    assert loaded.state.changed_files == {'module.py'}
+    assert loaded.state.evidence[EvidenceType.TESTS_PASSED] == 5
+    assert loaded.state.verification.records[0].kind == VerificationKind.TEST
+    assert loaded.progress.seen_milestones == {'plan_created'}
+    assert loaded.metrics.model_turns == 8
+    assert loaded.trace.events[0].event_type == 'tool'
+    assert loaded.history_turns == session.history_turns
+
+
+def test_safe_resume_after_completed_tool_batch(tmp_path):
+    root, store, session = setup_session(tmp_path)
+    store.save(session)
+    assistant = {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'type': 'function',
+        'function': {'name': 'write_file', 'arguments': '{}'}}]}
+    store.begin_turn(session, assistant)
+    store.before_tool(session, 'c1', 'write_file', {})
+    (root / 'module.py').write_text('print(2)\n', encoding='utf-8')
+    session.state.workspace_revision += 1
+    session.state.changed_files.add('module.py')
+    store.after_tool(session, {'role': 'tool', 'tool_call_id': 'c1', 'content': 'ok'})
+    store.finish_turn(session)
+    loaded = store.load()
+    assert loaded.inflight_turn is None
+    assert loaded.pending_tool is None
+    assert loaded.state.workspace_revision == 1
+    assert len(loaded.history_turns) == 1
+    assert loaded.history_turns[0][1]['tool_call_id'] == 'c1'
+
+
+def test_crash_after_tool_change_is_not_replayed(tmp_path):
+    root, store, session = setup_session(tmp_path)
+    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
+    store.before_tool(session, 'c2', 'delete_file', {})
+    (root / 'module.py').unlink()  # side effect happened, no ToolResult saved
+    with pytest.raises(RecoveryRequired, match='UNCERTAIN tool delete_file'):
+        store.load()
+
+
+def test_crash_with_partial_turn_requires_review(tmp_path):
+    _, store, session = setup_session(tmp_path)
+    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
+    with pytest.raises(RecoveryRequired, match='Partial model'):
+        store.load()
+
+
+def test_external_change_rejected(tmp_path):
+    root, store, session = setup_session(tmp_path)
+    store.save(session)
+    (root / 'module.py').write_text('external edit\n', encoding='utf-8')
+    with pytest.raises(RecoveryRequired, match='Workspace changed'):
+        store.load()
+
+
+def test_wrong_workspace_rejected(tmp_path):
+    root, store, session = setup_session(tmp_path)
+    store.save(session)
+    other = tmp_path / 'other'; other.mkdir()
+    wrong = CheckpointStore(store.path, other)
+    with pytest.raises(CheckpointError, match='another workspace'):
+        wrong.load()
+
+
+def test_corrupt_checkpoint_rejected(tmp_path):
+    _, store, session = setup_session(tmp_path)
+    store.save(session)
+    data = store.path.read_text(encoding='utf-8')
+    store.path.write_text(data.replace('refactor module', 'refactor others'), encoding='utf-8')
+    with pytest.raises(CheckpointError, match='checksum mismatch'):
+        store.load()
+
+
+def test_cannot_finish_with_pending_tool(tmp_path):
+    _, store, session = setup_session(tmp_path)
+    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
+    store.before_tool(session, 'c1', 'write_file', {})
+    with pytest.raises(CheckpointError, match='incomplete turn'):
+        store.finish_turn(session)
+
+
+def test_unsupported_schema_rejected(tmp_path):
+    _, store, session = setup_session(tmp_path)
+    store.save(session)
+    import json
+    import hashlib
+    envelope = json.loads(store.path.read_text(encoding='utf-8'))
+    envelope['payload']['schema'] = 999
+    payload = envelope['payload']
+    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
+                      separators=(',', ':'), allow_nan=False).encode()
+    envelope['sha256'] = hashlib.sha256(body).hexdigest()
+    store.path.write_text(json.dumps(envelope), encoding='utf-8')
+    with pytest.raises(CheckpointError, match='Unsupported checkpoint schema'):
+        store.load()
+
+
+def test_run_agent_resumes_state_and_tool_history(tmp_path, monkeypatch):
+    """Integration test with scripted model calls; no real API required."""
+    from types import SimpleNamespace
+    from mini_coding_agent import agent
+
+    monkeypatch.setattr(agent, 'WORKSPACE', tmp_path)
+    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 1)
+    monkeypatch.setattr(agent, 'compress_history', lambda **kw: None)
+    monkeypatch.setattr(agent, 'print_fixed_context_breakdown', lambda **kw: None)
+    monkeypatch.setattr(agent, 'print_context_debug', lambda **kw: None)
+    monkeypatch.setattr(agent, 'print_state_debug', lambda state: None)
+    monkeypatch.setattr(agent.AgentTrace, 'print_trace', lambda self: None)
+    monkeypatch.setattr(agent.AgentMetrics, 'print_summary', lambda self: None)
+
+    class FakeMessage:
+        def __init__(self, calls=None):
+            self.tool_calls = calls or []
+            self.content = ''
+        def model_dump(self, **kw):
+            return {'role': 'assistant', 'content': '', 'tool_calls': [
+                {'id': c.id, 'type': 'function', 'function': {'name': c.function.name,
+                                                         'arguments': c.function.arguments}}
+                for c in self.tool_calls
+            ]}
+
+    call = SimpleNamespace(id='call-1', function=SimpleNamespace(
+        name='set_goal', arguments='{"objective": "make the code safer"}'))
+    model_calls = []
+    def fake_model(*args, **kwargs):
+        model_calls.append(kwargs)
+        if len(model_calls) == 1:
+            return FakeMessage([call])
+        return FakeMessage()
+    monkeypatch.setattr(agent, 'call_model', fake_model)
+
+    cp = tmp_path / '.mini_agent' / 'test.json'
+    first = agent.run_agent('make the code safer', checkpoint_path=str(cp))
+    assert 'Outcome: stopped' in first
+    loaded = CheckpointStore(cp, tmp_path).load()
+    assert loaded.metrics.model_turns == 1
+    assert loaded.state.goal.locked
+    assert len(loaded.history_turns) == 1
+    assert len(loaded.history_turns[0]) == 2
+
+    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 3)
+    second = agent.run_agent('', checkpoint_path=str(cp), resume=True)
+    assert 'Outcome: stopped' in second
+    loaded = CheckpointStore(cp, tmp_path).load()
+    assert loaded.metrics.model_turns == 3
+    assert len(loaded.history_turns) == 2
+    assert loaded.history_turns[0][1]['tool_call_id'] == 'call-1'
```

## 九、完整测试源码 `tests/test_checkpoint.py`

```python
"""Lesson 25 checkpoint tests: no live model calls."""
from pathlib import Path

import pytest

from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.checkpoint import (
    CheckpointError, CheckpointStore, RecoveryRequired, RunSession,
)
from mini_coding_agent.evidence import EvidenceType
from mini_coding_agent.planning import (
    PlanSnapshot, PlanStatus, TaskStatus, TodoItem, TodoKind,
    create_agent_state,
)
from mini_coding_agent.requirements import RequirementKind, TaskRequirement
from mini_coding_agent.runtime_lifecycle import ProgressTracker
from mini_coding_agent.verification import VerificationKind, VerificationRecord


def setup_session(tmp_path: Path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'module.py').write_text('print(1)\n', encoding='utf-8')
    store = CheckpointStore(root / '.mini_agent' / 'checkpoint.json', root)
    session = RunSession(state=create_agent_state('refactor module'))
    return root, store, session


def test_full_state_roundtrip(tmp_path):
    root, store, session = setup_session(tmp_path)
    state = session.state
    state.goal.locked = True
    state.goal.objective = 'refactor module'
    state.requirements.locked = True
    state.requirements.items = [
        TaskRequirement(id=1, kind=RequirementKind.MUST_CHANGE,
                        description='update module', target='module.py')
    ]
    state.plan.revision = 2
    state.plan.replan_count = 1
    state.plan.items = [
        TodoItem(id=1, content='Change', status=TaskStatus.COMPLETED,
                 kind=TodoKind.IMPLEMENTATION, requirement_ids=[1]),
        TodoItem(id=2, content='Test', depends_on=[1],
                 required_evidence=EvidenceType.TESTS_PASSED,
                 reuse_from_task_id=3),
    ]
    state.plan.history = [
        PlanSnapshot(revision=1, items=[TodoItem(id=3, content='Old')],
                     status=PlanStatus.SUPERSEDED, reason='blocked',
                     created_turn=4, superseded_turn=7)
    ]
    state.workspace_revision = 5
    state.code_revision = 3
    state.changed_files = {'module.py'}
    state.evidence = {EvidenceType.TESTS_PASSED: 5}
    state.verification.records = [
        VerificationRecord(VerificationKind.TEST, True, 5, 3,
                           'pytest', 'all passed')
    ]
    session.progress = ProgressTracker(last_meaningful_turn=7,
                                       seen_milestones={'plan_created'})
    session.metrics = AgentMetrics(model_turns=8, tool_calls=10)
    session.history_turns = [[{'role': 'user', 'content': 'earlier check'}]]
    session.trace.record(7, 'tool', 'pytest', 'passed')
    store.save(session)
    loaded = store.load()
    assert loaded.state.goal.objective == 'refactor module'
    assert loaded.state.plan.history[0].items[0].id == 3
    assert loaded.state.plan.items[1].depends_on == [1]
    assert loaded.state.plan.items[1].reuse_from_task_id == 3
    assert loaded.state.changed_files == {'module.py'}
    assert loaded.state.evidence[EvidenceType.TESTS_PASSED] == 5
    assert loaded.state.verification.records[0].kind == VerificationKind.TEST
    assert loaded.progress.seen_milestones == {'plan_created'}
    assert loaded.metrics.model_turns == 8
    assert loaded.trace.events[0].event_type == 'tool'
    assert loaded.history_turns == session.history_turns


def test_safe_resume_after_completed_tool_batch(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    assistant = {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'type': 'function',
        'function': {'name': 'write_file', 'arguments': '{}'}}]}
    store.begin_turn(session, assistant)
    store.before_tool(session, 'c1', 'write_file', {})
    (root / 'module.py').write_text('print(2)\n', encoding='utf-8')
    session.state.workspace_revision += 1
    session.state.changed_files.add('module.py')
    store.after_tool(session, {'role': 'tool', 'tool_call_id': 'c1', 'content': 'ok'})
    store.finish_turn(session)
    loaded = store.load()
    assert loaded.inflight_turn is None
    assert loaded.pending_tool is None
    assert loaded.state.workspace_revision == 1
    assert len(loaded.history_turns) == 1
    assert loaded.history_turns[0][1]['tool_call_id'] == 'c1'


def test_crash_after_tool_change_is_not_replayed(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'c2', 'delete_file', {})
    (root / 'module.py').unlink()  # side effect happened, no ToolResult saved
    with pytest.raises(RecoveryRequired, match='UNCERTAIN tool delete_file'):
        store.load()


def test_crash_with_partial_turn_requires_review(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    with pytest.raises(RecoveryRequired, match='Partial model'):
        store.load()


def test_external_change_rejected(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    (root / 'module.py').write_text('external edit\n', encoding='utf-8')
    with pytest.raises(RecoveryRequired, match='Workspace changed'):
        store.load()


def test_wrong_workspace_rejected(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    other = tmp_path / 'other'; other.mkdir()
    wrong = CheckpointStore(store.path, other)
    with pytest.raises(CheckpointError, match='another workspace'):
        wrong.load()


def test_corrupt_checkpoint_rejected(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.save(session)
    data = store.path.read_text(encoding='utf-8')
    store.path.write_text(data.replace('refactor module', 'refactor others'), encoding='utf-8')
    with pytest.raises(CheckpointError, match='checksum mismatch'):
        store.load()


def test_cannot_finish_with_pending_tool(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'c1', 'write_file', {})
    with pytest.raises(CheckpointError, match='incomplete turn'):
        store.finish_turn(session)


def test_unsupported_schema_rejected(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.save(session)
    import json
    import hashlib
    envelope = json.loads(store.path.read_text(encoding='utf-8'))
    envelope['payload']['schema'] = 999
    payload = envelope['payload']
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()
    envelope['sha256'] = hashlib.sha256(body).hexdigest()
    store.path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(CheckpointError, match='Unsupported checkpoint schema'):
        store.load()


def test_run_agent_resumes_state_and_tool_history(tmp_path, monkeypatch):
    """Integration test with scripted model calls; no real API required."""
    from types import SimpleNamespace
    from mini_coding_agent import agent

    monkeypatch.setattr(agent, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 1)
    monkeypatch.setattr(agent, 'compress_history', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_fixed_context_breakdown', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_context_debug', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_state_debug', lambda state: None)
    monkeypatch.setattr(agent.AgentTrace, 'print_trace', lambda self: None)
    monkeypatch.setattr(agent.AgentMetrics, 'print_summary', lambda self: None)

    class FakeMessage:
        def __init__(self, calls=None):
            self.tool_calls = calls or []
            self.content = ''
        def model_dump(self, **kw):
            return {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': c.id, 'type': 'function', 'function': {'name': c.function.name,
                                                         'arguments': c.function.arguments}}
                for c in self.tool_calls
            ]}

    call = SimpleNamespace(id='call-1', function=SimpleNamespace(
        name='set_goal', arguments='{"objective": "make the code safer"}'))
    model_calls = []
    def fake_model(*args, **kwargs):
        model_calls.append(kwargs)
        if len(model_calls) == 1:
            return FakeMessage([call])
        return FakeMessage()
    monkeypatch.setattr(agent, 'call_model', fake_model)

    cp = tmp_path / '.mini_agent' / 'test.json'
    first = agent.run_agent('make the code safer', checkpoint_path=str(cp))
    assert 'Outcome: stopped' in first
    loaded = CheckpointStore(cp, tmp_path).load()
    assert loaded.metrics.model_turns == 1
    assert loaded.state.goal.locked
    assert len(loaded.history_turns) == 1
    assert len(loaded.history_turns[0]) == 2

    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 3)
    second = agent.run_agent('', checkpoint_path=str(cp), resume=True)
    assert 'Outcome: stopped' in second
    loaded = CheckpointStore(cp, tmp_path).load()
    assert loaded.metrics.model_turns == 3
    assert len(loaded.history_turns) == 2
    assert loaded.history_turns[0][1]['tool_call_id'] == 'call-1'
```

## 十、验证与边界

本地运行：`PYTHONPATH=test_stubs:src pytest -q tests`，其中 `test_stubs` 是**测试环境临时放置的 OpenAI 导入替身**（本容器没有安装 openai SDK），不在交付代码包内。当前得到 **84 passed**，包含本课 10 个测试；没有实际调用模型 API。

技术边界：

- `Checkpoint` 不是源码备份；恢复前必须保证源码仍然存在且一致。Fingerprint 对工作区扫描有开销，不适合超大仓库直接照搬。
- 通过 `run_command` 间接改文件时，你项目原有 `record_workspace_change()` 可能不更新 Revision；后续应该在工具执行器中统一检测实际改动，这是已有代码的缺口。
- 目前没有“自动判定不确定副作用并修复状态”的功能；遇到 pending 时停下来核对，安全性高于无人值守恢复率。
- 目前没有跨进程文件锁；一个任务不能由两个 Runner 同时操作同一 Checkpoint / Workspace。
- 仅可保证已有 dataclass schema 的反序列化；未来 State 字段变化需要版本迁移。
- 文件路径应放在工具可编辑的 Workspace **之外**，权限应限制给本人，否则 Agent 工具可能修改状态文件本身。

## 十一、思考题

1. 为什么 `workspace_revision` 需要和 `VerificationRecord` 一起保存？
2. 当 `delete_file` 已成功，但 `after_tool` 保存之前断电，恢复时为什么不能自动再执行一次？
3. 为什么不能只在每一轮结束后保存？
4. 如果模型完成一次 `replan()` 后还没保存，旧 Plan 与磁盘源码怎样出现分歧？
5. 当 Checkpoint 中的 `history_turns` 已保存，而 `ProgressTracker` 还未保存时，如何在启动时通过 `observe()` 补足？

## 下一课

第 26 课：Tool Safety / Human-in-the-loop。我们会重点讨论有副作用工具的权限、用户审批、拒绝之后的任务状态以及可审计的操作流程。
