# 用户命令

## 概述

`kokoro/user_commands.py` 实现了自然语言命令检测和执行，让用户可以通过语音直接要求 AI "看看屏幕"。

## 命令检测

### 支持的命令类型

| 类型 | 触发短语示例 |
|------|-------------|
| `screen.inspect` | "帮我看看屏幕"、"你看得见我在干嘛吗"、"分析一下这个页面"、"read this screen" 等 |

### 检测机制

1. 从用户语音文本中匹配多组正则模式（`SCREEN_COMMAND_PATTERNS`）
2. 结合动作词（看/瞅/扫/look/scan）和对象词（屏幕/窗口/page/screen）综合判断
3. 排除负向表达（"不用看""别看"）和自我描述（"我自己看了半天"）

### 置信度

- 精准匹配预定义模式 → `confidence = 0.95`
- 通用动作+对象匹配 → `confidence = 0.75`

## 命令执行

`execute(command)` 接收检测到的命令并执行：

1. **前置检查**：检测前台窗口是否涉及隐私（密码、支付、会议等），是则返回 privacy 结果
2. **视觉分析**：使用专门的 `user_commands.screen_inspect_prompt` 提示词调用视觉 API，重点读取前台窗口的文字内容（标题、正文、按钮、错误信息、输入框、列表项、状态提示）
3. **结果组装**：返回 `CommandResult`，包含原始屏幕描述和格式化上下文

## CommandResult

```python
@dataclass(frozen=True)
class CommandResult:
    type: str                # 命令类型
    ok: bool                 # 是否成功
    context: str             # 格式化后的对话上下文（含用户原话）
    screen_context: str      # 原始屏幕描述
    score: float             # 兴趣度（指令触发固定 100.0）
    private: bool            # 是否隐私内容
    user_visible_note: str   # 给用户的反馈文本
    error: str               # 错误信息
```

## 等待回应

`build_waiting_reply()` 在视觉分析完成前生成一句自然的等待回应（如"好，我看一下"），避免用户感觉冷场：

- 调用小模型（复用 STT 精炼的 LLM）生成等待式回应
- 参考角色设定和最近上下文，保持语气一致
- 6-18 个汉字，简洁自然

## CLI 集成

`cli.py` 中，用户文本先经过 `user_commands.detect()`：

```
用户输入 → detect() 检测到命令 → 等待回应 → 视觉分析 → 注入结果上下文
    ↓                                      ↓
  未检测到命令                           指令成功/失败
    ↓                                      ↓
  正常对话流程                         插入 system 消息继续对话
```
