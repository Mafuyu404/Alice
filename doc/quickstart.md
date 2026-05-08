# 快速开始

本文给出从安装到启动的最短路径。更完整的模块说明见同目录其他文档。

## 环境要求

- Windows
- Python 3.11+
- 可用的 LLM 后端，例如 Ollama、DeepSeek，或任意 OpenAI 兼容 `/v1/chat/completions` 服务
- 麦克风，仅语音模式需要

## 安装依赖

按实际功能安装即可。

```bash
# 基础
pip install requests numpy

# 语音识别
pip install sounddevice sherpa-onnx

# 立绘和屏幕感知
pip install PySide6 pillow pywin32

# TTS 后端，按需安装
pip install websockets
pip install cartesia

# 长期记忆，按需安装
pip install mem0ai fastembed
```

## 配置

`config.toml` 是主配置文件，适合提交。`config.json` 用来放本地密钥，已被 `.gitignore` 忽略。

常用配置：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"

memory_backend = "mem0"      # mem0 / kokoromemo / none
tts_backend = "minimax"      # minimax / cartesia

portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decay_seconds = 60.0
portrait_click_through = false
```

`config.json` 示例：

```json
{
  "deepseek_api_key": "sk-xxx",
  "minimax_api_key": "sk-xxx",
  "cartesia_api_key": "sk-xxx",
  "vision_api_key": "sk-xxx",
  "tts_voice_id": "xxx"
}
```

部分密钥也可使用环境变量，例如 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`。

## 角色

角色来自 `characters/` 目录：

```text
characters/{id}/{id}.json
characters/{id}/portrait/portrait.json
characters/{id}/portrait/*.png
```

当前仓库中已有：

- `alice`：有角色设定和 96 张立绘
- `penglai`：有角色设定和 7 张立绘
- `yuki`：有角色设定，暂无 PNG 立绘

根目录的 `characters.json` 是旧版聚合文件，当前主流程不再以它作为角色来源。

## 启动 CLI

完整语音模式：

```bash
python cli.py
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--character alice` | 指定角色 ID，默认 `alice` |
| `-c penglai` | `--character` 的短写 |
| `--model qwen2.5:7b` | 临时指定对话模型 |
| `--device 0` | 指定麦克风设备 ID |
| `--list-devices` | 列出可用麦克风设备 |
| `--no-tts` | 禁用语音输出 |
| `--no-portrait` | 禁用立绘覆盖层 |
| `--no-impulse` | 禁用主动搭话 |
| `--no-screen-watch` | 禁用屏幕感知 |

示例：

```bash
python cli.py --character penglai --no-screen-watch
python cli.py --character alice --model deepseek-v4-flash
```

## 快速检查

确认 LLM 后端可用：

```bash
curl http://127.0.0.1:11434/api/tags
```

确认角色能被加载：

```bash
python -c "from kokoro import character; print(character.load().keys())"
```

确认立绘说明能对应到文件：

```bash
python -c "import json,pathlib; r=pathlib.Path('characters/penglai/portrait'); d=json.loads((r/'portrait.json').read_text(encoding='utf-8')); print(len(d), [x['id'] for x in d if not (r/x['id']).exists()])"
```

输出中的缺失列表应为空。

## 常见问题

- PowerShell 直接 `Get-Content` 中文文档出现乱码：通常是控制台代码页显示问题，文件本身仍是 UTF-8。
- `Character 'xxx' not found`：检查是否存在 `characters/xxx/xxx.json`。
- 立绘窗口没有出现：确认安装了 `PySide6`，并检查 `characters/{id}/portrait/portrait.json` 中的 `id` 是否对应 PNG。
- 端口 17352 被占用：修改 `portrait_overlay_port`，或关闭已有立绘窗口。
