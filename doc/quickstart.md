# 快速开始

## 前置要求

- Windows 10/11
- Python 3.11+
- 一个可用的 LLM 服务（本地 Ollama 或 DeepSeek API）

---

## 第一步：安装依赖

```bash
pip install requests numpy websockets pywin32 pillow
pip install sherpa-onnx sounddevice
pip install PySide6
pip install mem0ai
```

按需安装 TTS 后端：

```bash
# MiniMax（国内推荐，低延迟）
# 无需额外 pip 包，WebSocket 原生连接

# Cartesia（海外）
pip install cartesia
```

## 第二步：配置

复制最小配置：

```toml
# config.toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
memory_backend = "mem0"
tts_backend = "minimax"
tts_volume = 1.0
```

密钥（`config.json`，不提交 git）：

```json
{
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "..."
}
```

## 第三步：启动

**快速测试（文本模式，无语音）**：

```bash
python text_cli.py
```

**完整桌面模式**：

```bash
python cli.py
```

参数示例：

```bash
# 指定角色
python cli.py --character penglai

# 关闭 TTS（纯文本调试）
python cli.py --no-tts

# 关闭立绘
python cli.py --no-portrait

# 查看可用麦克风
python cli.py --list-devices
```

## 第四步：多人对话

```bash
# 交互模式
python run_multi.py --chars alice,penglai

# 看板模式（角色自动对话）
python run_multi.py --watch --chars alice,penglai --topic "我们一起随便聊聊吧"
```

## 第五步：Edge 网页缓存（可选）

以调试端口启动 Edge：

```bat
msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\alice-edge-debug"
```

确保 `config.toml` 中：

```toml
[edge_page_cache]
enabled = true
```

## 查看记忆

```bash
python memory_viewer.py
```

---

## 常见问题

### "No module named sherpa_onnx"
STT 模块需要额外安装。如果不需要语音输入，可以用 `text_cli.py`。

### 控制台中文显示为 `?`
Windows 非 UTF-8 编码导致。使用 `text_cli.py` 或浏览器查看中文内容。

### DeepSeek API 返回 401
检查 `config.json` 中的 `deepseek_api_key` 是否正确，以及环境变量 `DEEPSEEK_API_KEY` 是否被设置。

### 麦克风没有声音
运行 `python cli.py --list-devices` 查看可用设备，用 `--device` 指定：

```bash
python cli.py --device 2
```

### TTS 没有声音
先确认 `tts_backend` 对应的 API key 已配置。使用 `--no-tts` 启动确认问题是否在 TTS 侧。
