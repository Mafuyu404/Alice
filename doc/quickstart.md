# 快速开始

## 1. 安装基础依赖

```bash
pip install requests numpy websockets pywin32 pillow
```

完整语音模式还需要：

```bash
pip install sherpa-onnx sounddevice
pip install PySide6
```

按需要安装：

```bash
pip install mem0ai fastembed
pip install cartesia
```

## 2. 配置 LLM

本地 Ollama 示例：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "qwen2.5:7b"
```

DeepSeek 示例：

```toml
llm_model = "deepseek-v4-flash"
deepseek_api_key = ""
```

把密钥放在 `config.json`：

```json
{
  "deepseek_api_key": "sk-..."
}
```

## 3. 运行精简文字模式

用于人格测试和提示词迭代：

```bash
python text_cli.py
```

无记忆、无写入、无认知评估的干净测试：

```bash
python text_cli.py --no-memory --no-store --no-cognition
```

关闭项目文件工具：

```bash
python text_cli.py --no-tools
```

只允许读文件：

```bash
python text_cli.py --read-only-tools
```

## 4. 运行完整桌面模式

```bash
python cli.py
```

常用参数：

```bash
python cli.py --character alice
python cli.py --model qwen2.5:7b
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --no-impulse
python cli.py --no-screen-watch
python cli.py --list-devices
```

## 5. Edge 网页缓存

先在 `config.toml` 开启：

```toml
[edge_page_cache]
enabled = true
interval_seconds = 15.0
```

用调试端口启动 Edge：

```powershell
Start-Process msedge -ArgumentList "--remote-debugging-port=9222 --user-data-dir=D:\tmp\alice-edge-debug"
```

缓存文件默认写入：

```text
data/edge_page_cache.json
```

## 6. 常见问题

LLM 连接失败：

- 检查 `llm_url`
- 检查模型服务是否启动
- DeepSeek 模型需要 API key

工具调用不稳定：

- 小模型可能不稳定
- 使用 `--no-tools`
- 或减少 `[tool_calling].tools`

TTS 太大或太小：

```toml
tts_volume = 0.5
```

Edge 缓存报 9222 连接失败：

- 当前 Edge 不是用 `--remote-debugging-port=9222` 启动的
- 关闭 Edge 后用上面的命令启动一个独立调试实例
