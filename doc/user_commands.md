# 工具调用与用户命令

项目里有两套“让模型做事”的机制：

- 完整 CLI 的 OpenAI function calling 工具。
- 文字 CLI 的项目内文件工具。

旧版自然语言正则命令仍在 `kokoro/user_commands.py`，但当前主路径优先使用 tool calling。

## 完整 CLI 工具

配置：

```toml
[tool_calling]
enabled = true
tools = [
  "look_at_screen",
  "search_memory",
  "get_current_time",
  "get_current_app",
  "save_to_memory",
]
max_iterations = 5
tool_timeout = 45.0
```

工具：

| 工具 | 作用 |
| --- | --- |
| `look_at_screen` | 即时截图并视觉分析 |
| `search_memory` | 搜索长期记忆 |
| `get_current_time` | 获取当前时间 |
| `get_current_app` | 获取前台窗口标题和进程 |
| `save_to_memory` | 保存长期记忆 |

关闭：

```bash
python cli.py --no-tools
```

## 文字 CLI 文件工具

`text_cli.py` 使用独立工具：

| 工具 | 作用 |
| --- | --- |
| `list_project_files` | 列出项目内目录 |
| `read_project_file` | 读取项目内 UTF-8 文本文件 |
| `write_project_file` | 覆盖写入项目内 UTF-8 文本文件 |

限制：

- 只能访问项目目录内相对路径。
- 不允许绝对路径。
- 不允许越界到项目外。
- 不执行命令。

关闭工具：

```bash
python text_cli.py --no-tools
```

只读：

```bash
python text_cli.py --read-only-tools
```

## 注意

小模型对 function calling 的稳定性可能较差。如果出现误触发、参数格式错、无限调用，优先：

- 使用 `--no-tools`
- 减少工具列表
- 增大模型
- 降低 `max_iterations`
