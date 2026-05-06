# 快速开始

## 环境要求

- Python 3.11+
- Windows（立绘叠加层依赖 Win32 API；屏幕识别需要 `pywin32` + `pillow`）
- 麦克风（语音模式需要）

## 安装依赖

```bash
# 核心依赖
pip install requests numpy sounddevice sherpa-onnx pillow pywin32

# TTS（可选）
pip install websockets    # MiniMax TTS
pip install cartesia      # Cartesia TTS

# 长期记忆（可选）
pip install mem0ai fastembed

# 立绘窗口（可选）
pip install PySide6
```

## 配置

### 1. 基本配置

`config.toml` 是主配置文件，已包含所有可配置项及详细注释。核心配置：

```toml
# LLM 地址（兼容 OpenAI 格式）
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"

# 记忆后端："mem0"、"kokoromemo" 或 "none"
memory_backend = "mem0"

# TTS 后端："minimax" 或 "cartesia"
tts_backend = "minimax"
```

### 2. API 密钥（可选）

创建 `config.json`（已加入 `.gitignore`，不会被提交）：

```json
{
  "deepseek_api_key": "sk-xxx",
  "minimax_api_key": "sk-xxx",
  "cartesia_api_key": "sk-xxx",
  "vision_api_key": "sk-xxx",
  "tts_voice_id": "xxx"
}
```

部分密钥也可通过环境变量设置：`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`。

### 3. 角色配置

`characters.json` 中预制了 `alice` 和 `yuki` 两个角色。可通过 Web UI 或直接编辑该文件来添加/修改角色。详见 [character.md](character.md)。

## 启动

### 语音模式（完整体验）

```bash
python cli.py
```

可用参数：

| 参数 | 作用 |
|------|------|
| `--character alice` | 指定角色 ID（默认 alice） |
| `--model qwen2.5:7b` | 指定对话模型（覆盖 config.toml） |
| `--device 0` | 指定麦克风设备 ID |
| `--list-devices` | 列出可用麦克风设备 |
| `--no-tts` | 禁用语音输出 |
| `--no-portrait` | 禁用立绘叠加层 |
| `--no-proactive` | 禁用主动搭话 |
| `--no-screen-watch` | 禁用屏幕识别 |

启动后会显示各模块状态：
```
==================================================
  Alice CLI
  Character: 爱丽丝·玛格特罗伊德
  Model: deepseek-v4-flash
  Microphone: [1]
  TTS: True
  Portrait: True
  Proactive: True
  Screen watch: True
  Memory events: True
  Ctrl+C to stop
==================================================
```

### 文字模式

```bash
python webui.py
```

打开 http://localhost:8080 即可在浏览器中对话。可通过 `--port`、`--host`、`--model`、`--character` 参数自定义。

WebUI 启动时会自动尝试连接 LLM 后端，如果 `LLM_BACKEND_CMD` 环境变量已设置且 LLM 不可用，则自动启动后端进程。当 `memory_backend = "kokoromemo"` 时，也会自动启动 `kokoromemo-server.exe`。

## 快速测试

确认 LLM 服务可用：

```bash
# Ollama
curl http://localhost:11434/api/tags

# 测试聊天（文字模式）
python webui.py
```

## 目录结构

```
├── cli.py                          # 语音 CLI 入口
├── webui.py                        # Web UI 入口
├── config.toml                     # 主配置文件（所有可配置项 + 注释）
├── config.json                     # 本地密钥（已 gitignore）
├── characters.json                 # 角色定义
├── prompts.json                    # 所有提示词集中管理
├── portrait_notes.json             # 立绘注释（供 LLM 选图用）
├── overlay_slideshow.py            # 立绘窗口（PySide6）
├── img/                            # 立绘素材
│   └── portrait_map.json           # 立绘素材映射
├── doc/                            # 文档
│   ├── overview.md                 # 框架概述
│   ├── quickstart.md               # 本文件
│   ├── config.md                   # 配置系统
│   ├── character.md                # 角色系统
│   ├── chat_session.md             # 对话会话与 LLM 客户端
│   ├── prompts.md                  # 提示词管理
│   ├── state_machine.md            # 状态机
│   ├── stt.md                      # 语音识别
│   ├── tts.md                      # 语音合成
│   ├── memory.md                   # 记忆系统
│   ├── proactive.md                # 主动搭话调度器
│   ├── screen_interest.md          # 屏幕兴趣检测
│   ├── portrait.md                 # 立绘系统
│   ├── webui.md                    # Web UI
│   └── user_commands.md            # 用户命令
├── models/
│   └── stt/                        # sherpa-onnx 模型（自动下载）
├── mem0_data/                      # mem0 本地向量库数据
└── kokoro/
    ├── __init__.py                 # 模块入口
    ├── config.py                   # 配置加载
    ├── state_machine.py            # 两级层次状态机
    ├── character.py                # 角色管理
    ├── prompts.py                  # 提示词加载
    ├── chat_session.py             # 对话会话
    ├── llm_client.py               # LLM 客户端
    ├── pool.py                     # STT 精炼池
    ├── stt.py                      # 语音识别
    ├── tts.py                      # TTS 调度（动态加载）
    ├── tts_minimax.py              # MiniMax TTS
    ├── tts_cartesia.py             # Cartesia TTS
    ├── memory.py                   # 记忆后端
    ├── memory_events.py            # 记忆事件检测
    ├── proactive.py                # 主动搭话调度器
    ├── screen_interest.py          # 屏幕兴趣检测
    ├── vision.py                   # 视觉识别 + 截图 + 窗口枚举
    ├── portrait_controller.py      # 立绘控制器
    └── user_commands.py            # 用户命令检测与执行
```
