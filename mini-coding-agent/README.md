# Mini Coding Agent - 知识点总结

这是一个用于学习 Agent 开发的入门项目，实现了一个简单的代码编辑助手。

## 核心知识点

### 1. Agent 基础架构
- **LLM 集成**: 使用 OpenAI 兼容 API（GLM-4）作为核心推理引擎
- **工具调用 (Function Calling)**: 实现了 LLM 调用外部工具的机制
- **多轮对话维护**: 通过 input_items 列表保存完整对话历史，支持上下文感知的交互
- **循环执行模式**: 最多执行 MAX_STEPS 步，直到 LLM 停止调用工具

### 2. 工具系统设计
- **工具定义格式**: 使用 JSON Schema 定义工具的名称、描述和参数结构
- **参数验证**: 对 LLM 返回的 JSON 参数进行解析和类型检查
- **统一执行接口**: execute_tool 函数路由到具体的工具函数
- **错误处理**: 捕获 JSON 解析错误和工具执行错误，返回友好提示

### 3. 文件操作工具
- **list_files**: 使用 Path.iterdir() 遍历目录，区分文件和文件夹
- **read_file**: 读取文件内容，超过 20000 字符时自动截断
- **write_file**: 写入文件，使用 mkdir(parents=True, exist_ok=True) 自动创建父目录
- **replace_text**: 精确文本替换，检查 old_text 出现次数避免歧义

### 4. 安全机制
- **工作空间隔离**: resolve_path 验证路径是否在 WORKSPACE 范围内，防止路径穿越攻击
- **危险命令拦截**: BLOCKED_COMMANDS 列表过滤 rm、sudo 等危险命令
- **命令超时控制**: subprocess 设置 30 秒超时，防止无限等待
- **路径解析**: 使用 Path.resolve() 规范化路径，防止相对路径绕过检查

### 5. 交互循环
- **用户输入**: while True 循环接收用户任务
- **Agent 推理**: 调用 LLM API，传递系统提示词、历史消息和工具定义
- **工具执行**: 解析 tool_calls，逐个执行工具并收集结果
- **结果反馈**: 将工具执行结果添加到对话历史，供 LLM 继续推理

### 6. System Prompt 设计
- **角色定义**: 明确 Agent 是在软件项目内工作的编码助手
- **能力说明**: 列出可以执行的操作（检查文件、修改文件、运行命令）
- **规则约束**: 制定 10 条操作规则，指导 Agent 行为
- **最佳实践**: 强调先检查后修改、尽量使用 replace_text、运行测试验证等

### 7. Python 技术点
- **pathlib.Path**: 现代化的路径操作库，比 os.path 更安全易用
- **subprocess.run**: 执行 shell 命令，捕获 stdout/stderr 和返回码
- **OpenAI SDK**: 使用标准化的 API 调用 LLM，支持工具调用
- **异常处理**: try-except 捕获文件不存在、权限错误等异常
- **类型提示**: 函数参数使用类型注解（如 path: str）提高代码可读性

### 8. 项目结构
```
mini-coding-agent/
├── src/mini_coding_agent/
│   ├── main.py           # 主程序，包含 Agent 逻辑和工具实现
│   └── demon/            # 演示目录
│       └── calculator.py # 示例文件
├── pyproject.toml        # 项目配置和依赖管理
└── README.md             # 项目文档
```

### 9. 工程实践
- **配置管理**: 环境变量读取 API_KEY，支持硬编码 fallback
- **模块化设计**: 工具函数独立，职责单一
- **日志输出**: 打印请求/响应 JSON 和工具执行过程，便于调试
- **命令行接口**: 提供 CLI 命令 `mini-coding-agent`

### 10. LLM 交互技巧
- **工具描述优化**: 描述中包含使用场景和注意事项
- **参数说明详细**: 每个参数都有清晰的 description
- **必填字段**: required 明确标识必填参数
- **additionalProperties: False**: 禁止额外参数，减少幻觉
- **排除 None 字段**: model_dump(exclude_none=True) 减少token消耗

## 使用示例

```bash
# 启动 Agent
mini-coding-agent

# 交互示例
You > 查看当前目录有哪些文件
You > 读取 calculator.py 的内容
You > 把 add 函数改名为 sum
You > 运行测试
```

## 依赖
- Python >= 3.11
- openai >= 3.14.0
- GLM-4 API (智谱 AI)

## 作者
jingjun - 254032134@qq.com