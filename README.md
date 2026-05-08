# Alice Chat

Alice Chat 是一个桌面 AI 陪伴框架，支持语音对话、文字 WebUI、角色设定、立绘覆盖层、主动搭话、屏幕感知和长期记忆。

项目当前有两个主要入口：

- `cli.py`：完整语音模式，包含 STT、LLM、TTS、立绘、主动搭话和屏幕感知。
- `webui.py`：浏览器文字模式，提供文本聊天、角色列表、模型切换和 OpenAI 兼容聊天接口。

核心代码在 `kokoro/`，角色数据在 `characters/`，配置在 `config.toml` 和本地密钥文件 `config.json`。

## 快速开始

### 环境

- Windows
- Python 3.11+
- 一个 OpenAI 兼容 LLM 服务，例如 Ollama、本地 `/v1/chat/completions` 服务或 DeepSeek 路由

常用依赖：

```bash
pip install requests numpy
pip install sherpa-onnx sounddevice
pip install PySide6 pillow pywin32
pip install websockets
pip install cartesia
pip install mem0ai fastembed
```

按实际功能安装即可：只用 WebUI 时不需要 STT、TTS、PySide6；只用本地无记忆模式时不需要 mem0。

### 配置

主要配置写在 `config.toml`。真实 API key 不要提交到 Git，可以写入已忽略的 `config.json` 或环境变量。

常用项：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
memory_backend = "mem0" # mem0 / kokoromemo / none
tts_backend = "minimax" # minimax / cartesia

portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decay_seconds = 60.0
portrait_click_through = false
```

### 启动

```bash
python cli.py
python cli.py --character penglai
python cli.py --model qwen2.5:7b
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --no-proactive
python cli.py --no-screen-watch
python cli.py --list-devices

python webui.py
```

WebUI 默认访问 `http://127.0.0.1:8080`。

## 当前目录结构

```text
.
├── cli.py                         # 语音入口
├── webui.py                       # 文字 WebUI 入口
├── index.html                     # WebUI 前端
├── overlay_slideshow.py           # 透明立绘窗口和 HTTP 控制服务
├── config.toml                    # 主配置
├── config.json                    # 本地密钥，已忽略
├── prompts.json                   # LLM 提示词
├── characters.json                # 旧版聚合角色文件，当前运行时不再作为主入口
├── characters/
│   ├── alice/
│   │   ├── alice.json
│   │   └── portrait/
│   │       ├── portrait.json
│   │       └── *.png
│   ├── penglai/
│   │   ├── penglai.json
│   │   └── portrait/
│   │       ├── portrait.json
│   │       └── *.png
│   └── yuki/
│       ├── yuki.json
│       └── portrait/
│           └── portrait.json
├── kokoro/                        # 核心模块
├── doc/                           # 详细文档
├── models/                        # STT 模型缓存
└── mem0_data/                     # 本地向量记忆数据
```

## 角色与立绘

角色按目录组织：

```text
characters/{character_id}/{character_id}.json
characters/{character_id}/portrait/portrait.json
characters/{character_id}/portrait/*.png
```

`kokoro.character.load()` 会扫描 `characters/` 下所有含同名 JSON 的目录。启动 CLI 时用 `--character` 指定角色，默认 `alice`。

立绘说明文件 `portrait.json` 是数组，每项只需要：

```json
[
  {
    "id": "penglai_seated_hands_lap_quiet_neutral_p01.png",
    "notes": "正坐合手放在膝前，直视前方，眼神空灵，嘴角轻收。"
  }
]
```

`id` 必须对应同目录下的 PNG 文件。`notes` 会交给立绘选择 LLM，用于根据对话内容挑选合适差分。

## 模块索引

| 文档 | 内容 |
| --- | --- |
| [overview.md](doc/overview.md) | 系统架构和运行流程 |
| [quickstart.md](doc/quickstart.md) | 启动与排障 |
| [config.md](doc/config.md) | 配置项 |
| [character.md](doc/character.md) | 角色目录和角色 JSON |
| [portrait.md](doc/portrait.md) | 立绘覆盖层、HTTP API 和立绘素材 |
| [stt.md](doc/stt.md) | 语音识别 |
| [tts.md](doc/tts.md) | 语音合成 |
| [memory.md](doc/memory.md) | 长期记忆 |
| [proactive.md](doc/proactive.md) | 主动搭话 |
| [screen_interest.md](doc/screen_interest.md) | 屏幕感知 |
| [webui.md](doc/webui.md) | WebUI |

## 注意事项

- 不要提交真实 API key。
- `config.toml` 是主配置，`config.json` 适合放本机密钥。
- 当前运行时角色主入口是 `characters/` 目录，不是根目录的 `characters.json`。
- WebUI 是文字模式；语音、立绘和主动搭话主要在 CLI 中运行。
- 立绘窗口会把位置和缩放保存到 `portrait_overlay_state.json`。
- 工具调用（tool calling / function calling）默认启用，使用 OpenAI 兼容格式。小模型（≤3B）对工具调用的支持不稳定，容易出现误触发或参数格式错误。如果使用 `qwen2.5:1.5b` 等小模型，建议通过 `--no-tools` 关闭，或只保留少数简单工具（如 `get_current_time`）。详见 `doc/config.md` 的「工具调用」章节。
