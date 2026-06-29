# Alice Chat

Alice Chat 是一个以内在叙事流为核心的本地化 AI 生命体实验框架。它把语音、QQ、屏幕、网页、时间流逝、工具结果和自身行动都视为信息事件，再由内在叙事流驱动说话、沉默、观察、搜索、记忆、表情和任务等行动能力。

项目当前包含两条主要入口：

- `cli.py`：完整桌面模式，包含 STT、TTS、立绘、字幕、屏幕/网页上下文、多人对话和长期记忆
- `text_cli.py`：轻量文本模式，适合调试提示词、人格、记忆和多角色调度

## 当前状态

当前主线实现已经切到以下方案：

- 记忆后端：`mem0`
- 记忆 embedding：`Ollama + bge-m3:latest`
- 记忆数据目录：项目根目录 `mem0_data/`
- 稠密检索：启用
- BM25 稀疏检索：关闭
- 多角色语音输入：支持
- 随机 MC 页面场景：支持

## 环境要求

- Windows
- Python 3.11+
- 一个可用的 LLM 服务：
  - 本地 `Ollama`
  - 或 DeepSeek 兼容接口

## 安装

常用依赖：

```bash
pip install requests numpy websockets pywin32 pillow
pip install sherpa-onnx sounddevice
pip install PySide6
pip install mem0ai
pip install cartesia
```

如果启用 `memory_backend = "mem0"`，还需要本地 `Ollama` 和 embedding 模型：

```bash
ollama pull bge-m3:latest
```

## 最小配置

`config.toml`：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
memory_backend = "mem0"
tts_backend = "minimax"
tts_volume = 1.0
```

本地私密密钥放 `config.json`：

```json
{
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "...",
  "cartesia_api_key": "...",
  "vision_api_key": "..."
}
```

## 运行

完整桌面模式：

```bash
python cli.py
python cli.py --character penglai
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --list-devices
```

文本模式：

```bash
python text_cli.py
python text_cli.py --no-memory --no-store --no-cognition
python text_cli.py --read-only-tools
```

多人 watch 模式：

```bash
python run_multi.py --watch --chars alice,penglai --topic "我们一起随便聊聊吧"
```

记忆查看器：

```bash
python memory_viewer.py
```

## 目录说明

```text
characters/      角色数据
doc/             文档
prompts/         TOML ???? prompts/skills ????
kokoro/core/     生命周期核心：事件与内在叙事流
kokoro/action/   行动模型、行动批次与行动运行时
kokoro/          包根目录，仅包含 core 和 action
mem0_data/       本地长期记忆数据
logs/            CLI 日志
config.toml      主配置
config.json      本地密钥覆盖
```

## 主要能力

- 内在叙事流驱动的持续状态
- 单角色 / 多角色对话
- 语音输入 / 语音输出
- AEC 回声消除
- 屏幕上下文 / Edge 网页缓存
- 行动能力：说话、沉默、搜索、观察、记忆、认知更新、QQ 行动、VTS 表达
- 随机 MC 页面讲解
- 长期记忆 / 认知 / 情绪
- 立绘与字幕叠加层
- Bilibili 直播弹幕接入

## 记忆后端说明

当前推荐方案：

- `mem0.llm.provider = "ollama"`
- `mem0.embedder.provider = "ollama"`
- `mem0.embedder.model = "bge-m3:latest"`

记忆目录结构：

- 根目录固定为 `mem0_data/`
- 不同 embedding 模型使用不同子目录
- 每个子目录包含：
  - 本地 qdrant 数据
  - `history.db`

## 文档入口

核心设计：

- [生命周期架构](doc/lifecycle.md) — 信息事件、内在叙事流、行动能力和结果回流
- [架构概览](doc/overview.md) — 当前模块如何落到生命周期架构上
- [内在叙事流](doc/inner_stream.md) — 连续主体状态和更新节奏
- [行动能力与工具](doc/user_commands.md) — 说话、沉默、搜索、观察、记忆等能力化
- [自主系统 Roadmap](doc/autonomous_roadmap.md) — 后续重构顺序

模块参考：

- [快速开始](doc/quickstart.md) / [配置说明](doc/config.md)
- [角色系统](doc/character.md) / [会话层](doc/chat_session.md) / [提示词系统](doc/prompts.md)
- [对话调度器](doc/dialogue_orchestrator.md) / [多角色调度器](doc/multi_dialogue_orchestrator.md)
- [STT](doc/stt.md) / [TTS](doc/tts.md) / [对话输入层](doc/conversation.md)
- [记忆系统](doc/memory.md) / [认知指南](doc/cognition_iteration_guide.md) / [情绪指南](doc/emotion_iteration_guide.md)
- [屏幕兴趣度](doc/screen_interest.md) / [Edge 页面缓存](doc/edge_page_cache.md) / [Bilibili 直播](doc/bilibili_live.md)
- [立绘](doc/portrait.md) / [字幕](doc/subtitle.md) / [VTS 集成](doc/vts_integration_plan.md)

## 注意

- 不要提交真实 API key
- `config.toml` 可提交，`config.json` 仅本地使用
- Windows 非 UTF-8 控制台可能把中文显示成 `?`；优先使用浏览器或 UTF-8 输出查看中文内容
- `text_cli.py` 的文件工具只允许访问项目目录内文件
