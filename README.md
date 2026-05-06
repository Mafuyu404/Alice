# Alice Chat

桌面 AI 陪伴框架，支持语音/文字对话、立绘呈现、主动搭话、屏幕感知和长期记忆。

两个入口：
- `cli.py` — 语音模式（STT 输入 + TTS 输出 + 立绘 + 主动搭话 + 屏幕监控）
- `webui.py` — 文字模式（浏览器聊天 + 角色管理）

共享逻辑在 `kokoro/`，配置文件 `config.toml`，提示词 `prompts.json`，角色 `characters.json`。详细文档在 `doc/`。

## 快速开始

### 环境

Python 3.11+，Windows（立绘和屏幕监控需要 Win32 API）。

### 安装

```bash
# 核心
pip install requests numpy

# 语音模式
pip install sherpa-onnx sounddevice

# TTS（二选一）
pip install websockets    # MiniMax
pip install cartesia      # Cartesia

# 可选
pip install mem0ai fastembed   # 长期记忆
pip install PySide6            # 立绘
pip install pillow pywin32     # 屏幕监控
```

### 配置

`config.toml`（已包含所有可配置项及注释），核心几项：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "qwen2.5:1.5b"
memory_backend = "none"
tts_backend = "minimax"
```

API 密钥放 `config.json`（已 gitignore）或环境变量。

### 启动

```bash
python cli.py                   # 语音模式
python cli.py --no-tts          # 只识别不说话
python cli.py --no-proactive    # 关闭主动搭话
python cli.py --no-screen-watch # 关闭屏幕监控
python cli.py --list-devices    # 列出麦克风

python webui.py                 # 文字模式，打开 http://127.0.0.1:8080
```

## 架构

```
麦克风 / 浏览器
      │
      ▼
状态机 (kokoro/state_machine.py)
IDLE ⇄ LISTENING ⇄ THINKING ⇄ SPEAKING
      │
      ├─ STT (sherpa-onnx 流式识别)
      ├─ Pool (碎片累积 + 静默检测 + LLM/本地精炼)
      ├─ UserCommands (自然语言指令检测)
      ├─ ChatSession (角色设定 + 记忆 + 屏幕上下文)
      ├─ LLM Client (OpenAI 兼容 API + SSE 解析)
      ├─ TTS (MiniMax WebSocket / Cartesia SSE)
      ├─ Portrait (LLM 选立绘 + PySide6 透明窗口)
      └─ Memory (mem0 / KokoroMemo / none)
      │
后台 worker（受状态机 is_busy 控制）:
      ├─ Proactive (冲动值驱动的主动搭话)
      ├─ Screen Watch (周期性截图分析 + 隐私过滤)
      ├─ Memory Events (日期纪念日 + 定期记忆查询)
      └─ Error Recovery (自动恢复 + 连续 3 次升级 FATAL)
```

## 项目结构

```
├── cli.py                     # 语音入口
├── webui.py                   # Web 入口
├── index.html                 # Web 前端
├── config.toml                # 配置
├── prompts.json               # 提示词
├── characters.json            # 角色
├── portrait_notes.json        # 立绘注释
├── overlay_slideshow.py       # 立绘窗口
├── img/                       # 立绘素材
├── doc/                       # 文档
├── models/                    # STT 模型缓存
├── mem0_data/                 # 本地向量库
└── kokoro/
    ├── state_machine.py       # 状态机
    ├── config.py              # 配置加载
    ├── character.py           # 角色管理
    ├── prompts.py             # 提示词加载
    ├── chat_session.py        # 对话会话
    ├── llm_client.py          # LLM 客户端
    ├── pool.py                # STT 精炼池
    ├── stt.py                 # 语音识别
    ├── tts.py                 # TTS 调度
    ├── tts_minimax.py         # MiniMax TTS
    ├── tts_cartesia.py        # Cartesia TTS
    ├── memory.py              # 记忆后端
    ├── memory_events.py       # 记忆事件
    ├── proactive.py           # 主动搭话
    ├── screen_interest.py     # 屏幕兴趣
    ├── vision.py              # 视觉识别
    ├── portrait_controller.py # 立绘控制
    └── user_commands.py       # 用户指令
```

## 主要模块

详见 `doc/` 下对应文档。

| 模块 | 文档 | 说明 |
|------|------|------|
| 状态机 | [state_machine.md](doc/state_machine.md) | 事件驱动，原子 `emit()` 抢对话槽位，错误自动恢复 |
| STT | [stt.md](doc/stt.md) | sherpa-onnx 流式识别 + 去噪 + 三种精炼模式 |
| TTS | [tts.md](doc/tts.md) | MiniMax/Cartesia 双后端动态加载 |
| 主动搭话 | [proactive.md](doc/proactive.md) | 冲动值模型（IDLE/RECENT/MEM/SCREEN）+ 干扰值过滤 |
| 屏幕监控 | [screen_interest.md](doc/screen_interest.md) | 周期性截图分析 + 隐私过滤 |
| 用户指令 | [user_commands.md](doc/user_commands.md) | "帮我看看屏幕" 等自然语言指令 |
| 记忆 | [memory.md](doc/memory.md) | mem0 / KokoroMemo / none 三种后端 |
| 立绘 | [portrait.md](doc/portrait.md) | LLM 选表情 + PySide6 透明窗口 |
| 角色 | [character.md](doc/character.md) | 角色定义与系统提示词构建 |
| 配置 | [config.md](doc/config.md) | 双层配置覆盖 + 所有配置项说明 |

## 注意事项

- 不要提交 API 密钥，用 `config.json` 或环境变量
- `config.toml` 管行为，`prompts.json` 管提示词措辞
- mem0 报 Qdrant 锁错误时，关掉其他占用 `mem0_data/` 的进程
- WebUI 纯文字，语音相关功能只在 CLI 可用
