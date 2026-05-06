# 用户命令

## 概述

`kokoro/user_commands.py` 实现了自然语言命令检测和执行，让用户可以通过语音直接要求 AI "看看屏幕"。

## 命令检测

### 支持的命令类型

| 类型 | 常量 | 描述 |
|------|------|------|
| `screen.inspect` | `TYPE_SCREEN_INSPECT` | 用户要求查看/分析屏幕内容 |

### 检测机制

`detect(text)` 函数使用多层策略：

1. **预处理**：`_normalize(text)` 去除所有空白字符
2. **排除检查**：
   - `NEGATIVE_RE` — 排除负向表达（"不用看""别看""don't look"）
   - `NON_COMMAND_RE` — 排除自我描述（"我看了半天屏幕眼睛累了"）
3. **精准匹配**：6 组 `SCREEN_COMMAND_PATTERNS` 正则模式，覆盖中英文自然表达
4. **通用匹配**：`SCREEN_ACTION_RE`（看/瞅/look/scan 等） + `SCREEN_OBJECT_RE`（屏幕/窗口/page 等）

### 置信度

- 精准匹配预定义模式 — `confidence = 0.95`
- 通用动作 + 对象匹配 — `confidence = 0.75`

### 正则模式覆盖

6 组模式覆盖以下表达类型：
1. 中文请求式：帮我看看屏幕 / 你能看一下这个页面吗
2. 中文对象式：看看我的屏幕 / 分析一下我的桌面
3. 中文疑问式：屏幕上有什么 / 这个界面显示什么
4. 中文上下文式：下一步怎么办 / 用什么技能
5. 英文指令式：look at my screen / read this page
6. 英文疑问式：what's on my screen / what is on this page

## 命令执行

`execute(command, timeout=45)` 接收检测到的命令并执行：

1. **前置检查**：调用 `vision.get_foreground_app()` 获取前台窗口 — `screen_interest.foreground_is_private()` 检查隐私。涉及隐私则返回 `privacy_context` + `privacy_note`
2. **视觉分析**：调用 `vision.detect_desktop()` — 全屏截图 + 枚举所有运行窗口 + 调用视觉 API，使用 `user_commands.screen_inspect_prompt` 作为分析提示（注入 `{user_text}`）
3. **结果组装**：返回 `CommandResult`，通过 `format_context()` 使用 `user_commands.screen_result_context` 模板格式化（注入 `{screen_content}` `{user_text}`）

`detect_desktop()` 与普通屏幕监控 `analyze()` 的区别：
- 使用全屏截图 + 窗口枚举组合，信息更全面
- 注入 `vision.analyze_suffix`（含运行窗口列表）
- 使用独立的命令专用提示词

## CommandResult

```python
@dataclass(frozen=True)
class CommandResult:
    type: str                # 命令类型
    ok: bool                 # 是否成功
    context: str             # 格式化后的对话上下文（注入 LLM 消息中）
    screen_context: str      # 原始屏幕描述（存入 session.screen_contexts）
    score: float             # 兴趣度（指令触发固定 100.0）
    private: bool            # 是否隐私内容
    user_visible_note: str   # 给用户的反馈文本（打印到终端）
    error: str               # 错误信息
```

## 等待回应

`build_waiting_reply()` 在视觉分析完成前生成一句自然的等待回应（如"好，我看一下"），避免用户感觉冷场：

- 调用小模型（复用 STT 精炼 LLM 的地址和模型）生成等待式回应
- 参考角色系统提示词和最近 6 条对话历史，保持语气一致
- 使用 `user_commands.waiting_system` + `user_commands.waiting_user` 提示词模板
- 回应经 `_clean_waiting_reply()` 清洗（去引号、去前缀、去空白、截断至 40 字符）
- 失败时降级到 `user_commands.waiting_fallback`
- 等待回应通过 TTS 播放同时视觉分析在后台进行（并行优化）

## CLI 集成

`cli.py` 的 `on_refined()` 回调中，用户文本先经过 `user_commands.detect()`。命令检测到后：

1. 状态机发出 `COMMAND_DETECTED` 事件，系统进入 THINKING 状态
2. `_run_vision()` 在后台线程中执行屏幕分析
3. `build_waiting_reply()` 生成等待回应并播放 TTS（与 vision 并行）
4. `vision_ready.wait()` 等待视觉分析完成
5. 结果通过 `extra_context` 注入 LLM 对话

```
用户输入 — detect() 检测到命令
    |
    ├─ 未检测到命令 — 正常对话流程
    |
    └─ 检测到命令
         |
         ├─ _run_vision() 后台线程（与等待回应并行）
         ├─ build_waiting_reply() + TTS 播放
         ├─ vision_ready.wait()
         └─ result.context 注入 — build_messages(extra_context=...) — chat_stream()
```

状态机会在对话期间（THINKING — SPEAKING）自动阻止屏幕监控和主动搭话的并发干扰，不再需要旧版的 `cancel_active_screen_watch()` 手动取消。
