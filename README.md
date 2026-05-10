# Alice Chat

Alice Chat 是一个桌面 AI 陪伴与人格实验框架。它有两种主要入口：

- `cli.py`：完整桌面模式，包含语音输入、语音输出、立绘、字幕、屏幕感知、主动搭话、直播弹幕和工具调用。
- `text_cli.py`：精简文字模式，只保留文字输入/输出和可选的项目内文件工具，适合人格测试、提示词迭代和自动化评测。

核心代码在 `kokoro/`，角色数据在 `characters/`，全局配置在 `config.toml`，本地密钥覆盖在已忽略的 `config.json`。

## 快速开始

环境要求：

- Windows
- Python 3.11+
- 一个 OpenAI 兼容 LLM 服务，或 DeepSeek 云端 API

常用依赖：

```bash
pip install requests numpy websockets pywin32 pillow
pip install sherpa-onnx sounddevice
pip install PySide6
pip install mem0ai fastembed
pip install cartesia
```

只运行 `text_cli.py` 时通常只需要 `requests`，以及你选择的记忆后端依赖。

## 配置

主要配置写在 `config.toml`。真实 API key 不要提交到 Git，可写入 `config.json`：

```json
{
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "...",
  "cartesia_api_key": "...",
  "vision_api_key": "..."
}
```

最小可用配置示例：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
memory_backend = "none"
tts_backend = "minimax"
tts_volume = 1.0
```

## 运行

完整桌面模式：

```bash
python cli.py
python cli.py --character penglai
python cli.py --model qwen2.5:7b
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --no-impulse
python cli.py --no-screen-watch
python cli.py --list-devices
```

精简文字测试模式：

```bash
python text_cli.py
python text_cli.py --no-memory --no-store --no-cognition
python text_cli.py --read-only-tools
python text_cli.py --no-tools --no-memory --no-store --no-cognition
```

记忆查看器：

```bash
python memory_viewer.py
```

## 角色目录

运行时角色来自 `characters/{character_id}/`：

```text
characters/
  alice/
    alice.json
    config.toml
    cognition.json
    emotion.json
    portrait/
      portrait.json
      *.png
```

角色主文件必须与目录同名，例如 `characters/alice/alice.json`。根目录的 `characters.json` 是旧聚合格式，当前主流程不依赖它。

## 主要功能

- 语音输入：`kokoro/stt.py` + `kokoro/pool.py`
- LLM 对话：`kokoro/llm_client.py` + `kokoro/agent_loop.py`
- 工具调用：查看屏幕、搜索记忆、保存记忆、获取时间、获取前台应用
- 精简文字工具：项目内列文件、读文件、写文件
- 语音输出：MiniMax 或 Cartesia，支持 `tts_volume`
- 立绘和字幕：PySide6 覆盖层 + HTTP 控制
- 主动搭话：`kokoro/impulse.py`
- 屏幕感知：周期性截图分析
- Edge 网页缓存：周期性读取当前 Edge 标签页正文并覆盖缓存文件
- 直播弹幕：Bilibili 直播间弹幕缓冲与主动回复
- 记忆：`none`、`mem0`、`kokoromemo`
- 人格层：角色设定、认知缓存、情绪状态、对话摘要

## 文档

- [架构概览](doc/overview.md)
- [快速开始](doc/quickstart.md)
- [配置说明](doc/config.md)
- [文字测试 CLI](doc/text_cli.md)
- [角色系统](doc/character.md)
- [会话与人格层](doc/chat_session.md)
- [提示词](doc/prompts.md)
- [记忆](doc/memory.md)
- [主动搭话](doc/impulse.md)
- [屏幕感知](doc/screen_interest.md)
- [Edge 网页缓存](doc/edge_page_cache.md)
- [STT](doc/stt.md)
- [TTS](doc/tts.md)
- [立绘](doc/portrait.md)
- [字幕](doc/subtitle.md)
- [直播弹幕](doc/bilibili_live.md)
- [工具调用](doc/user_commands.md)
- [状态机](doc/state_machine.md)

## 注意

- 不要提交真实 API key。
- `config.toml` 是可提交的主配置，`config.json` 是本地密钥覆盖。
- `text_cli.py` 的文件工具只能访问项目目录内文件，不能执行命令。
- Edge 网页缓存需要用 `--remote-debugging-port` 启动 Edge。
- 小模型对 function calling 支持可能不稳定，必要时使用 `--no-tools`。
