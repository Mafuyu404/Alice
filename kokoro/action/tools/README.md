# 行动工具模块规范

`kokoro.action.tools` 是能力层。CLI 代码只是入口和运行时组装面：它可以调用公开工具门面来加载会话、创建运行时资源包、启动循环。可执行行为、本地路由规则、客户端、长生命周期资源，以及工具专属提炼逻辑，都属于对应工具模块。

## 目录形状

每个行动工具拥有一个独立目录：

```text
tools/<tool_name>/
  __init__.py    # 公开门面：只放 register(registry) 和稳定 helper
  spec.py        # schema 常量和 ToolSpec 注册
  prepare.py     # 可选：把 Action 转成可执行的 PreparedAction
  execute.py     # 执行 prepared action
  after.py       # 可选：执行后的后续行为
  runtime.py     # 可选：长生命周期资源或本地运行时 helper
  config.py      # 可选：工具自有默认值和配置加载
```

已注册行动工具必须把 `spec.py` 和 `execute.py` 放在工具目录里。纯查询工具可以省略 `prepare.py`；没有后续行为的工具可以省略 `after.py`。新行为不应该继续加到 `cli.py`、`tool_schemas.py` 或 `tool_handlers.py`。

仅运行时能力模块也放在 `tools/` 下，但它们不是注册的 `Action` 工具。例如输入源、桥接、直播运行时、文本 CLI helper。这些目录必须列在 `RUNTIME_MODULE_NAMES` 里，让包边界保持显式。仅运行时模块必须有 `__init__.py`，不能暴露 `register(registry)`，也不能包含 `spec.py`、`prepare.py`、`execute.py` 或 `after.py`。

## 生命周期

工具通过下面的入口注册：

```python
def register(registry: ActionToolRegistry) -> None:
    registry.register(ToolSpec(...))
```

`ToolSpec` 声明：

- `name`：公开工具能力名。
- `actions`：这个工具处理的所有 `Action.action` 值。
- `schema`：可选的 function-call schema，定义在本地 `spec.py`。
- `prepare`：可选准备阶段。
- `execute`：必需执行阶段。
- `after`：可选执行后 hook。
- `default_visibility` 和 `default_result_policy`：事件反馈行为。

已注册 hook 是模块契约的一部分：

- `execute` 必须由工具自己的 `execute.py` 实现。
- `prepare` 如果存在，必须由工具自己的 `prepare.py` 实现。
- `after` 如果存在，必须由工具自己的 `after.py` 实现。
- 已注册行动工具不能同时列在 `RUNTIME_MODULE_NAMES`。
- 每个 `Action.action` 值只归一个已注册 `ToolSpec` 所有。

## Prepare 规则

`prepare` 属于工具，不属于通用行动选择器。当工具在执行前需要聚焦处理时使用它：

- `say`：把意图转成最终说话上下文或文本。
- `search_web`：从行动理由和上下文中提取精确 query。
- `memory`：把事件细节整合成记忆内容。
- `observe_screen`：规范化视觉关注点。
- `qq` 和表情包：选择目标或候选集。

某个工具需要额外 LLM 调用时，应发生在这个工具的准备阶段，或由 `prepare` 调用的工具自有 helper 中。

行动选择器只决定是否使用某个能力。工具专属参数提炼、搜索 query 提取、记忆整合、最终说话塑形，都留在已选工具的 `prepare` 阶段，避免选择器膨胀出工具专属推理分支。

## 导入边界

核心行动模块可以导入工具包门面：

```python
from kokoro.action.tools import search_web
```

它们不应该导入工具内部模块，例如 `kokoro.action.tools.search_web.client`。CLI 运行时模块可以通过公开门面组装资源。`kokoro.action` 下的顶层旧模块应该只是很薄的兼容门面，等待旧导入逐步退场。

工具模块可以导入自己的内部文件。跨工具访问必须通过对方工具的包门面：

```python
from kokoro.action.tools import say as say_tool
```

不要从实现文件或包门面导入另一个工具的内部模块。工具门面是这个工具的公开边界；它不应该重新导出另一个工具的私有实现细节。

## 能力归属

附属行为放到它服务的能力下面：

- `say`：主动/本地说话、最终说话塑形、TTS、字幕、画像输出、回声过滤和 AEC。
- `speech_input`：麦克风、STT、语音轮次缓冲、重叠处理和语音派生文本事件。
- `search_web`：query 提取、网页客户端、daemon/运行时搜索 helper 和搜索结果反馈。
- `memory`：记忆搜索、记忆写入和记忆内容整合。
- `observe_screen`：屏幕视觉、前台应用、屏幕兴趣、视觉用户命令，以及页面/屏幕缓存。
- `qq`：QQ 桥接、输入、媒体、表情包支持；公开消息/表情动作通过行动工具注册。
- `vts`：表情、动作、VTS 控制器/运行时和身体驱动 helper。
- `live`：直播平台输入和运行时集成。
- `debug_input`：持久高优先级纯文本调试输入，以及日志驱动调试自动化。
- `task`：长任务创建、进度、列表和取消。

## 注册边界

`tools.__init__` 拥有注册清单：

- `TOOL_MODULES`：必须暴露 `spec.py` 和 `register(registry)` 的行动工具包。
- `TOOL_ACTIONS`：所有已注册 action name，包括 `say` 和 `wait` 这类非 function-call action。
- `DEFAULT_ENABLED_TOOL_ACTIONS`：旧 OpenAI 兼容工具循环默认启用的 function-call 工具。
- `RUNTIME_MODULE_NAMES`：提供运行时或输入能力、但不注册为行动工具的工具包目录。
- `register_all(registry)`：导入工具模块并注册它们的 spec。

`kokoro.action.tool_schemas` 只做兼容。新 schema 必须放到对应工具模块里。

## 反馈

每个工具结果必须返回 `ToolResult` 或字符串。`ActionRuntime` 会写入带有 `cycle_id`、`action_id` 和 `causality_id` 的 started/result 事件；工具应该把专属细节放进 `ToolResult.metadata`，而不是发布并行的临时结果通道。
