# 架构概览

Alice Chat 由一组松耦合模块组成：输入层负责语音或文字，调度层组织对话和状态，服务层连接 LLM/STT/TTS/记忆/视觉，呈现层负责立绘与 WebUI。

## 运行模式

### CLI 语音模式

`python cli.py` 启动完整桌面陪伴流程。

主要流程：

1. `kokoro.stt` 从麦克风读取音频，使用 sherpa-onnx 做流式识别。
2. `kokoro.pool` 收集 STT 文本片段，等待静默稳定后按配置精炼文本。
3. `kokoro.user_commands` 检测自然语言命令，例如主动查看屏幕。
4. `kokoro.chat_session` 组装 system prompt、对话历史、记忆上下文和屏幕上下文。
5. `kokoro.llm_client` 调用 OpenAI 兼容聊天接口并解析流式输出。
6. `kokoro.tts` 按 `tts_backend` 调度 MiniMax 或 Cartesia。
7. `kokoro.portrait_controller` 根据本轮对话从角色立绘目录中选择差分。
8. `kokoro.proactive` 在空闲时根据冲动值触发主动搭话。
9. `kokoro.screen_interest` 周期性分析屏幕，必要时生成主动上下文。
10. `kokoro.state_machine` 统一管理 listening、thinking、speaking、portrait 等状态。

常用参数：

```bash
python cli.py --character alice
python cli.py --character penglai
python cli.py --model deepseek-v4-flash
python cli.py --device 1
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --no-proactive
python cli.py --no-screen-watch
python cli.py --list-devices
```

### WebUI 文字模式

`python webui.py` 启动 FastAPI 服务和浏览器文字聊天界面。

WebUI 提供：

- `/`：返回 `index.html`
- `/api/health`：检查 LLM 和记忆状态
- `/api/models`：列出当前模型和可选模型
- `/api/models/switch`：切换当前模型
- `/api/characters`：读取角色列表
- `/v1/chat/completions`：OpenAI 兼容聊天接口

WebUI 不启动 STT、TTS、立绘窗口、主动搭话和屏幕感知。

## 数据流

```text
麦克风 / 浏览器
    ↓
STT / WebUI 请求
    ↓
ChatSession
    ├── 角色 system prompt
    ├── 对话历史
    ├── 记忆上下文
    └── 屏幕观察上下文
    ↓
LLM Client
    ↓
回复文本
    ├── TTS 播放
    ├── 立绘选择
    └── 记忆写入
```

## 当前角色结构

运行时角色来自 `characters/`：

```text
characters/
├── alice/
│   ├── alice.json
│   └── portrait/
│       ├── portrait.json
│       └── *.png
├── penglai/
│   ├── penglai.json
│   └── portrait/
│       ├── portrait.json
│       └── *.png
└── yuki/
    ├── yuki.json
    └── portrait/
        └── portrait.json
```

`kokoro.character.load()` 扫描子目录，并要求角色文件名与目录名一致。根目录的 `characters.json` 是旧版聚合格式，当前主流程不依赖它。

## 主要模块

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| CLI | `cli.py` | 语音模式总入口 |
| WebUI | `webui.py` | 文字聊天和 API |
| 配置 | `kokoro/config.py` | 读取 `config.toml`、`config.json` 和环境变量 |
| 角色 | `kokoro/character.py` | 扫描角色目录、构建 system prompt |
| 会话 | `kokoro/chat_session.py` | 对话历史、记忆注入、屏幕上下文 |
| LLM | `kokoro/llm_client.py` | OpenAI 兼容请求、SSE 解析、模型路由 |
| STT | `kokoro/stt.py` | 麦克风流式识别 |
| STT Pool | `kokoro/pool.py` | 文本片段聚合和精炼 |
| TTS | `kokoro/tts.py` | 动态选择 TTS 后端 |
| 记忆 | `kokoro/memory.py` | mem0、KokoroMemo 或 none |
| 主动搭话 | `kokoro/proactive.py` | 空闲触发、记忆触发、屏幕触发 |
| 屏幕感知 | `kokoro/screen_interest.py` | 截图分析、隐私过滤、兴趣评分 |
| 视觉 | `kokoro/vision.py` | 截图、窗口枚举、多模态接口 |
| 立绘控制 | `kokoro/portrait_controller.py` | 启动 overlay、选择立绘 |
| 立绘窗口 | `overlay_slideshow.py` | PySide6 透明窗口和 HTTP 控制 |
| 状态机 | `kokoro/state_machine.py` | 线程安全状态管理 |

## 外部依赖

| 能力 | 依赖 |
| --- | --- |
| LLM | Ollama、DeepSeek 或其他 OpenAI 兼容接口 |
| STT | `sherpa-onnx`、`sounddevice`、`numpy` |
| TTS MiniMax | `websockets` 和 MiniMax API key |
| TTS Cartesia | `cartesia` 和 Cartesia API key |
| 立绘覆盖层 | `PySide6` |
| 屏幕感知 | `pillow`、`pywin32`、视觉模型 API |
| 本地记忆 | `mem0ai`、`fastembed` |
