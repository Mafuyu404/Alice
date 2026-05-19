# 文本 CLI

入口文件：`text_cli.py`

## 用途

纯文本模式的调试入口。没有 STT/TTS/立绘/字幕，启动快，适合快速迭代和回归测试。

典型场景：
- 调整角色说话风格和提示词
- 验证记忆/认知/情绪是否跑偏
- 验证多轮对话是否稳定
- 调试工具调用
- 回归测试

## 启动方式

```bash
# 基础模式
python text_cli.py

# 关闭记忆
python text_cli.py --no-memory

# 关闭记忆存储（但仍可检索已有记忆）
python text_cli.py --no-store

# 关闭认知层
python text_cli.py --no-cognition

# 工具调用模式（只读工具，不修改状态）
python text_cli.py --read-only-tools

# 关闭工具调用（使用旧版正则命令匹配）
python text_cli.py --no-tools
```

## 工具模式

`text_cli.py` 支持两种工具调用模式：

### 完整工具模式（默认）

所有已启用的工具可用，包括 `save_to_memory` 等写操作。

### 只读工具模式（`--read-only-tools`）

仅允许读取操作：
- `look_at_screen`
- `search_memory`
- `get_current_time`
- `get_current_app`

写操作（`save_to_memory`）被阻止。

## 与 cli.py 的差异

| 特性 | text_cli.py | cli.py |
|---|---|---|
| 语音输入 | 无 | STT + AEC |
| 语音输出 | 无 | TTS |
| 立绘 | 无 | 透明立绘窗口 |
| 字幕 | 无 | 流式字幕 |
| 启动速度 | 快（秒级） | 慢（加载识别模型等） |
| 对话调度器 | 完整支持 | 完整支持 |
| 多角色 | 有限支持 | 完整支持（run_multi.py） |
| 工具调用 | 完整支持 | 完整支持 |

## 安全限制

文件工具的路径访问限制在项目目录内，防止路径穿越。
