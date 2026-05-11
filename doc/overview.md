# 架构概览

Alice Chat 由入口层、会话层、模型层、工具层、感知层、输出层和人格状态层组成。完整桌面模式面向日常陪伴，精简文字模式面向人格测试和提示词迭代。

## 入口

### `cli.py`

完整桌面入口。启动后会按配置初始化：

- 麦克风 STT（AEC 回声消除）
- 对话池（ConversationManager + 重叠分类器）
- ChatSession
- LLM streaming 或 agent tool loop
- TTS
- 立绘覆盖层
- 字幕覆盖层
- 屏幕感知缓存
- Edge 页面缓存
- 主动搭话 planner
- Bilibili 弹幕连接
- 记忆事件检查

### `text_cli.py`

精简文字入口。不启动 STT、TTS、屏幕识图、直播、立绘、字幕、主动搭话。它只做：

- 读取用户文本
- 构建对话 messages
- 调用 LLM
- 打印文本回复
- 可选记忆写入
- 可选项目内文件工具

适合自动化人格测试、提示词回归测试、角色文件迭代。

## 对话数据流

```text
用户输入
  -> ChatSession.build_messages()
     -> system prompt
     -> history
     -> conversation summary
     -> screen context
     -> memory context
     -> cognition runtime cache
     -> emotion state
     -> user message
  -> Agent loop 或 LLM streaming
  -> assistant reply
  -> ChatSession.remember()
     -> history append
     -> memory store
     -> cognition cache refresh
     -> emotion async evaluation
     -> periodic cognition evaluation
     -> optional conversation summary
```

## 主动搭话数据流

`kokoro/impulse.py` 会在空闲时规划并执行主动发言。规划输入包括：

- 角色规划 system prompt
- 当前时间
- 对话摘要
- 最近四轮对话
- 相关长期记忆
- 屏幕感知缓存
- Edge 当前网页缓存
- 当前计划表
- cognition runtime cache
- emotion state
- 直播模式下的弹幕上下文和用户列表

planner 输出计划表增删改操作，执行器按计划等待并触发一次普通对话生成。

## 主要模块

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 完整 CLI | `cli.py` | 桌面语音模式总入口 |
| 文字 CLI | `text_cli.py` | 人格测试和提示词迭代入口 |
| 配置 | `kokoro/config.py` | 加载 `config.toml` 和 `config.json` |
| 角色 | `kokoro/character.py` | 扫描角色目录，构建 system prompt |
| 会话 | `kokoro/chat_session.py` | 历史、摘要、记忆、认知、情绪 |
| LLM | `kokoro/llm_client.py` | OpenAI 兼容 streaming 请求 |
| Agent | `kokoro/agent_loop.py` | function calling 循环 |
| 工具 | `kokoro/tool_registry.py` | 内置工具注册与执行 |
| 文字工具 | `kokoro/text_cli_tools.py` | 项目内文件读写工具 |
| STT | `kokoro/stt.py` | 麦克风语音识别 |
| AEC | `kokoro/aec.py` | WebRTC 声学回声消除 |
| 对话调度 | `kokoro/conversation.py` | 自然对话事件驱动调度 |
| 重叠分类 | `kokoro/overlap.py` | 0.5B 模型判定插话打断级别 |
| TTS | `kokoro/tts.py` | TTS 后端分发 |
| 记忆 | `kokoro/memory.py` | none / mem0 / KokoroMemo |
| 认知 | `kokoro/cognition.py` | 长期认知条目和 runtime cache |
| 情绪 | `kokoro/emotion.py` | 当前情绪基调和中期动机 |
| 主动搭话 | `kokoro/impulse.py` | 空闲规划和主动发言 |
| 屏幕感知 | `kokoro/screen_interest.py` | 截图分析和缓存 |
| Edge 缓存 | `kokoro/edge_cache.py` | 当前 Edge 标签页正文缓存 |
| 状态机 | `kokoro/state_machine.py` | 完整模式运行状态 |

## 目录结构

```text
.
  cli.py
  text_cli.py
  overlay_slideshow.py
  overlay_subtitle.py
  config.toml
  config.json
  prompts.json
  kokoro/
  characters/
  doc/
  data/
  logs/
  mem0_data/
  models/
```

`data/`、`logs/`、`mem0_data/`、`models/` 都是运行时数据目录，默认不应提交。
