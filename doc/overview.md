# 框架概述

## 架构总览

Alice Chat 是一个桌面 AI 陪伴框架，围绕"语音对话 + 视觉呈现 + 环境感知 + 长期记忆"四个维度设计。用户通过语音或文字与角色互动，角色通过 TTS 语音回复并配合立绘表情变化，同时具备主动搭话和屏幕感知能力。

```
┌──────────────────────────────────────────────────────────┐
│                      用户输入层                            │
│   麦克风 (sherpa-onnx STT)      浏览器 (FastAPI WebUI)      │
└────────┬──────────────────────────┬───────────────────────┘
         ▼                          ▼
┌──────────────────────────────────────────────────────────┐
│                     调度中枢                               │
│  cli.py（语音模式） / webui.py（文字模式）                   │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐   │
│  │ Chat     │ │ User     │ │ Proactive│ │ Memory     │   │
│  │ Session  │ │ Commands │ │ Scheduler│ │ Events     │   │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘   │
└────────┬─────────────────────────────────────────────────┘
         ▼
┌──────────────────────────────────────────────────────────┐
│                     核心服务层                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐   │
│  │ LLM      │ │ TTS      │ │ STT      │ │ Vision     │   │
│  │ Client   │ │ Backends │ │ Pipeline │ │ (Screen    │   │
│  │          │ │          │ │          │ │  + Image)  │   │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘   │
└────────┬─────────────────────────────────────────────────┘
         ▼
┌──────────────────────────────────────────────────────────┐
│                     呈现层                                  │
│  ┌──────────────────────┐  ┌──────────────────────────┐   │
│  │  Portrait Overlay    │  │  扬声器（TTS 播放）         │   │
│  │  (PySide6 透明窗口)   │  │  浏览器（WebUI 页面）       │   │
│  └──────────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## 主要工作模式

### 语音模式（CLI）

`python cli.py` 启动完整的语音对话体验：

1. **STT 流式识别** — sherpa-onnx 实时识别麦克风输入，持续产出文本片段
2. **对话池精炼** — 碎片文本经静默检测后由小模型修正同音错字
3. **用户命令检测** — 识别"帮我看看屏幕"等指令，触发主动屏幕分析
4. **LLM 对话** — 携带角色设定、长期记忆、最近屏幕观察记录生成回复
5. **TTS 语音合成** — 回复文本通过 MiniMax/Cartesia 流式转为语音
6. **立绘同步** — 后台线程根据对话情绪选择立绘表情，空闲时自动衰减回平静
7. **主动搭话** — 空闲时根据冲动值模型触发角色主动说话（空闲/对话余韵/记忆/屏幕）
8. **屏幕监控** — 周期性截图分析，感知用户当前活动，结果注入对话历史
9. **记忆事件** — 定期检查日期纪念日和记忆后端查询

### 文字模式（WebUI）

`python webui.py` 启动浏览器界面：

- FastAPI 后端提供 REST API + SSE 流式响应
- 纯文字对话，不涉及 STT/TTS/立绘/主动搭话/屏幕监控
- 角色 CRUD、模型切换、历史对话管理

## 配置机制

双层配置覆盖：`config.toml`（主配置，可提交 git）+ `config.json`（密钥，已 gitignore）。TOML 值优先，JSON 填充空值。详见 [config.md](config.md)。

## 角色系统

`characters.json` 定义角色名称、描述、性格、背景、关系、问候语和主动搭话指导。系统提示词由 `prompts.json` 中的 `character_system.template` + `character_system.expression_calibration` 渲染。详见 [character.md](character.md)。

## 提示词管理

所有 LLM 提示词集中在 `prompts.json`，涵盖：角色设定、STT 精炼、记忆重要度判断、立绘选择、四种主动搭话、屏幕内容分析、屏幕指令检测、视觉分析。详见 [prompts.md](prompts.md)。

## 主要模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 配置加载 | `kokoro/config.py` | 双层配置合并、环境变量读取 |
| 角色管理 | `kokoro/character.py` | 角色加载、系统提示词构建 |
| 提示词管理 | `kokoro/prompts.py` | prompts.json 加载与格式化 |
| 对话会话 | `kokoro/chat_session.py` | 对话历史、屏幕上下文、记忆注入 |
| LLM 客户端 | `kokoro/llm_client.py` | OpenAI 兼容 API 流式调用 |
| 语音识别 | `kokoro/stt.py` | sherpa-onnx 流式 ASR |
| 对话池 | `kokoro/pool.py` | STT 碎片累积 + 静默检测 + 精炼触发 |
| 语音合成 | `kokoro/tts.py` | TTS 后端动态调度 |
| TTS MiniMax | `kokoro/tts_minimax.py` | WebSocket 流式 TTS |
| TTS Cartesia | `kokoro/tts_cartesia.py` | SSE 流式 TTS |
| 记忆后端 | `kokoro/memory.py` | 长期记忆（mem0/KokoroMemo/None） |
| 记忆事件 | `kokoro/memory_events.py` | 日期纪念日 + 定期记忆查询 |
| 主动搭话 | `kokoro/proactive.py` | 冲动值驱动的主动说话调度器 |
| 屏幕兴趣 | `kokoro/screen_interest.py` | 截图分析 + 隐私过滤 + 兴趣评分 |
| 视觉识别 | `kokoro/vision.py` | DashScope/Ollama 视觉 API 封装 |
| 立绘控制 | `kokoro/portrait_controller.py` | LLM 立绘选择 + 子进程管理 |
| 立绘窗口 | `overlay_slideshow.py` | PySide6 透明立绘窗口 |
| 用户命令 | `kokoro/user_commands.py` | "看看屏幕"等自然语言命令检测与执行 |
| CLI 入口 | `cli.py` | 语音模式主流程，各模块编排 |
| WebUI | `webui.py` | 浏览器文字聊天 + 角色管理 API |

## 外部依赖

| 服务 | 用途 | 必选 |
|------|------|------|
| Ollama / OpenAI 兼容 API | LLM 对话 | 是 |
| MiniMax / Cartesia | TTS 语音合成 | 否（语音模式） |
| sherpa-onnx + sounddevice | STT 语音识别 | 否（语音模式） |
| mem0ai + fastembed | 本地向量记忆 | 否 |
| DashScope / Ollama 多模态 | 视觉识别 | 否（屏幕监控） |
| PySide6 | 立绘透明窗口 | 否（立绘模式） |
