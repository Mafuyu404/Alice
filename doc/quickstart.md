# 快速开始

## 环境要求

- Python 3.10+
- Windows（立绘叠加层依赖 Win32 API）
- 麦克风（语音模式需要）

## 安装依赖

```bash
pip install -r requirements.txt
```

需要额外安装的可选依赖：

```bash
# 长期记忆（可选）
pip install mem0ai

# STT 语音识别（语音模式需要）
pip install sherpa-onnx sounddevice numpy
```

## 配置

### 1. 基本配置

复制 `config.toml` 并根据需要修改：

```toml
# LLM 地址（兼容 OpenAI 格式）
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"

# 记忆后端："mem0" 或 "none"
memory_backend = "mem0"
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

密钥也可通过环境变量设置：`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`。

### 3. 角色配置

`characters.json` 中预制了 `alice` 角色。可通过 Web UI 或直接编辑该文件来添加/修改角色。

## 启动

### 语音模式（完整体验）

```bash
python cli.py
```

可用参数：

| 参数 | 作用 |
|------|------|
| `--character alice` | 指定角色 ID（默认 alice） |
| `--model qwen2.5:7b` | 指定对话模型 |
| `--list-devices` | 列出可用麦克风设备 |
| `--no-tts` | 禁用语音输出 |
| `--no-portrait` | 禁用立绘叠加层 |
| `--no-proactive` | 禁用主动搭话 |
| `--no-screen-watch` | 禁用屏幕识别 |

### 文字模式

```bash
python webui.py
```

打开 http://localhost:8080 即可在浏览器中对话。

## 快速测试

确认 LLM 服务可用：

```bash
# Ollama
curl http://localhost:11434/api/tags

# 测试聊天
python webui.py --model qwen2.5:7b
```

## 目录结构

```
├── cli.py                          # 语音 CLI 入口
├── webui.py                        # Web UI 入口
├── config.toml                     # 主配置文件
├── config.json                     # 本地密钥（已 gitignore）
├── characters.json                 # 角色定义
├── prompts.json                    # 所有提示词
├── portrait_notes.json             # 立绘注释（用于 AI 选图）
├── kokoro/
│   ├── __init__.py                 # 模块入口
│   ├── config.py                   # 配置加载
│   ├── character.py                # 角色管理
│   ├── prompts.py                  # 提示词加载
│   ├── chat_session.py             # 对话会话
│   ├── llm_client.py               # LLM 客户端
│   ├── pool.py                     # STT 提炼池
│   ├── stt.py                      # 语音识别
│   ├── tts.py                      # TTS 调度
│   ├── tts_minimax.py              # MiniMax TTS
│   ├── tts_cartesia.py             # Cartesia TTS
│   ├── memory.py                   # 记忆后端
│   ├── memory_events.py            # 记忆事件检测
│   ├── proactive.py                # 主动搭话调度器
│   ├── screen_interest.py          # 屏幕兴趣检测
│   ├── portrait_controller.py      # 立绘控制器
│   └── vision.py                   # 视觉识别
├── overlay_slideshow.py            # 立绘窗口（Qt）
├── local_llm.py                    # 本地模型后备
└── img/                            # 立绘素材
```
