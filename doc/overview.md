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
│  ┌──────────┐                                             │
│  │ STT Pool │  (STT 碎片累积 + 静默检测 + 精炼触发)         │
│  └──────────┘                                             │
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

1. **STT 流式识别** — sherpa-onnx 实时识别麦克风输入，持续产出文本片段，经过 `denoise()` 去噪处理
2. **对话池精炼** — 碎片文本经静默检测（默认 1.0s）后，根据 `stt_refine_mode` 选择精炼策略：
   - `separate`：独立 LLM 调用修正同音错字
   - `inline`：本地正则预清洗 + 聊天 LLM 隐式纠错
   - `none`：仅 `local_clean_stt()` 正则清洗，零 LLM 开销
3. **用户命令检测** — 识别"帮我看看屏幕"等指令，触发主动屏幕分析
4. **LLM 对话** — 携带角色设定、长期记忆、最近屏幕观察记录生成回复
5. **TTS 语音合成** — 回复文本通过 MiniMax（WebSocket）/ Cartesia（SSE）流式转为语音
6. **立绘同步** — 后台线程根据对话情绪选择立绘表情，空闲时自动衰减回平静
7. **主动搭话** — 空闲时根据冲动值模型触发角色主动说话（IDLE / RECENT / MEM / SCREEN）
8. **屏幕监控** — 周期性截图分析，感知用户当前活动，结果注入对话历史
9. **记忆事件** — 定期检查日期纪念日和记忆后端查询

### 文字模式（WebUI）

`python webui.py` 启动浏览器界面：

- FastAPI 后端提供 REST API + SSE 流式响应
- 纯文字对话，不涉及 STT/TTS/立绘/主动搭话/屏幕监控
- 角色 CRUD、模型切换、自动启动 KokoroMemo / LLM 后端

## 配置机制

双层配置覆盖：`config.toml`（主配置，可提交 git）+ `config.json`（密钥，已 gitignore）。TOML 值优先，JSON 填充空值。详见 [config.md](config.md)。

## 角色系统

`characters.json` 定义角色名称、描述、性格、背景、关系、问候语、对话示例和主动搭话指导。系统提示词由 `prompts.json` 中的 `character_system.template` + `character_system.expression_calibration` 渲染。详见 [character.md](character.md)。

## 提示词管理

所有 LLM 提示词集中在 `prompts.json`，涵盖：角色设定、STT 精炼、inline 纠错、记忆重要度判断、立绘选择、四种主动搭话、屏幕内容分析、屏幕指令检测、视觉分析、等待回应生成。详见 [prompts.md](prompts.md)。

## 主要模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 配置加载 | `kokoro/config.py` | 双层配置合并、环境变量读取、辅助访问函数 |
| 角色管理 | `kokoro/character.py` | 角色加载/保存、系统提示词构建 |
| 提示词管理 | `kokoro/prompts.py` | prompts.json 加载、点号路径取值、模板格式化 |
| 对话会话 | `kokoro/chat_session.py` | 对话历史管理、屏幕上下文、记忆注入、消息构建 |
| LLM 客户端 | `kokoro/llm_client.py` | OpenAI 兼容 API 流式调用、SSE 解析、自动路由 |
| 语音识别 | `kokoro/stt.py` | sherpa-onnx 流式 ASR、音频去噪、设备选择、模型下载 |
| 对话池 | `kokoro/pool.py` | STT 碎片累积 + 静默检测 + 精炼触发 + local_clean_stt |
| 语音合成 | `kokoro/tts.py` | TTS 后端动态加载与调度（`__getattr__` 透传） |
| TTS MiniMax | `kokoro/tts_minimax.py` | WebSocket 流式 TTS，支持自动重连 |
| TTS Cartesia | `kokoro/tts_cartesia.py` | SSE 流式 TTS，多线程播放 |
| 记忆后端 | `kokoro/memory.py` | 长期记忆（mem0 / KokoroMemo / None），重要度过滤 |
| 记忆事件 | `kokoro/memory_events.py` | 日期纪念日 + 定期记忆查询 + 冷却机制 |
| 主动搭话 | `kokoro/proactive.py` | 冲动值驱动的主动说话调度器，四类行为 + 多样性 |
| 屏幕兴趣 | `kokoro/screen_interest.py` | 截图分析 + 隐私过滤 + 兴趣评分 + JSON 解析 |
| 视觉识别 | `kokoro/vision.py` | DashScope/Ollama 视觉 API、截图捕获、窗口枚举 |
| 立绘控制 | `kokoro/portrait_controller.py` | LLM 立绘选择 + 子进程管理 |
| 立绘窗口 | `overlay_slideshow.py` | PySide6 透明立绘窗口 + HTTP 控制服务 |
| 用户命令 | `kokoro/user_commands.py` | 自然语言命令检测、屏幕指令执行、等待回应生成 |
| CLI 入口 | `cli.py` | 语音模式主流程，各模块编排 + 多线程协调 |
| WebUI | `webui.py` | 浏览器文字聊天 + 角色管理 API + 后端自动启动 |

## 外部依赖

| 服务 | 用途 | 必选 |
|------|------|------|
| Ollama / OpenAI 兼容 API | LLM 对话 | 是 |
| MiniMax / Cartesia | TTS 语音合成 | 否（语音模式推荐） |
| sherpa-onnx + sounddevice + numpy | STT 语音识别 | 否（语音模式需要） |
| mem0ai + fastembed | 本地向量记忆 | 否 |
| DashScope / Ollama 多模态 | 视觉识别（屏幕监控） | 否 |
| PySide6 | 立绘透明窗口 | 否（立绘模式需要） |
| websockets | MiniMax TTS WebSocket | 否（MiniMax 后端需要） |
| cartesia | Cartesia TTS SDK | 否（Cartesia 后端需要） |
| pillow + pywin32 | 屏幕截图 + 窗口枚举 | 否（屏幕监控需要） |
