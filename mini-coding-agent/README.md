# Mini Coding Agent

一个用于学习 Agent 开发的入门项目：基于 OpenAI 兼容 API（GLM-4）实现的简单代码编辑助手，支持工具调用（Function Calling）、文件操作与安全命令执行。

## 功能

- 多轮对话维护与循环推理（最多 MAX_STEPS 步）
- 内置工具：`list_files`、`read_file`、`write_file`、`replace_text`、命令执行
- 工作空间路径隔离、危险命令拦截、命令超时控制

## 环境要求

- Python >= 3.11
- openai >= 3.14.0
- GLM-4 API Key（通过环境变量配置，见 `.env.example`）

## 快速开始

```bash
uv sync          # 安装依赖
mini-coding-agent  # 启动 Agent
```

交互示例：

```text
You > 查看当前目录有哪些文件
You > 把 add 函数改名为 sum
You > 运行测试
```

## 项目结构

```
mini-coding-agent/
├── src/mini_coding_agent/
│   └── main.py       # Agent 主逻辑与工具实现
├── tests/            # 测试
└── pyproject.toml    # 项目配置
```

## 运行测试

```bash
uv run pytest
```

## 作者

jingjun - 254032134@qq.com

## 工作区根目录路径

/Users/jingjun/Desktop/Githup/coding-agent/mini-coding-agent
