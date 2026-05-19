# Alice Chat

Alice Chat 是一个面向桌面陪伴、多角色对话和语音交互的本地化 AI 框架。项目当前包含两条主要入口：

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
kokoro/          核心运行时模块
mem0_data/       本地长期记忆数据
logs/            CLI 日志
config.toml      主配置
config.json      本地密钥覆盖
```

## 主要能力

- 单角色对话
- 多角色对话
- 语音输入 / 语音输出
- AEC 回声消除
- 屏幕上下文 / Edge 网页缓存
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

### 架构与配置
- [架构概览](doc/overview.md) — 分层结构、数据流、设计决策
- [快速开始](doc/quickstart.md) — 从零到运行
- [配置说明](doc/config.md) — 完整配置参考
- [状态机](doc/state_machine.md) — 系统状态定义与事件驱动

### 对话与角色
- [角色系统](doc/character.md) — 角色目录结构、system prompt 构建
- [会话与人格层](doc/chat_session.md) — 历史、摘要、上下文注入、异步维护链
- [对话调度器](doc/dialogue_orchestrator.md) — 话轮判断 + 主动搭话 + 计划执行
- [多角色调度器](doc/multi_dialogue_orchestrator.md) — 谁说、对谁说、自动续接、预取
- [提示词系统](doc/prompts.md) — 所有 LLM 提示词目录与设计原则

### 语音
- [STT](doc/stt.md) — 语音识别、模型、AEC、精炼模式
- [对话输入层](doc/conversation.md) — 端点检测、重叠分类、回声过滤
- [TTS](doc/tts.md) — 语音合成、流式控制、多角色串行

### 记忆与人格
- [记忆系统](doc/memory.md) — 向量记忆、事件提取、生命周期
- [认知层迭代指南](doc/cognition_iteration_guide.md) — 边界检查、测试场景
- [情绪层迭代指南](doc/emotion_iteration_guide.md) — 评估流程、稳定性规则
- [记忆测试](doc/memory_test.md) — 写入/检索/质量验证

### 上下文感知
- [屏幕兴趣度](doc/screen_interest.md) — 桌面截图分析、隐私检测
- [Edge 页面缓存](doc/edge_page_cache.md) — 浏览器正文读取、MC 场景
- [Bilibili 直播](doc/bilibili_live.md) — 弹幕接收、场景集成

### 显示与集成
- [立绘](doc/portrait.md) — 表情选择、衰减、多角色
- [字幕](doc/subtitle.md) — 流式字幕、双实例
- [文本 CLI](doc/text_cli.md) — 调试工具、只读模式
- [VTS 集成](doc/vts_integration_plan.md) — Live2D 表情、口型同步

## 注意

- 不要提交真实 API key
- `config.toml` 可提交，`config.json` 仅本地使用
- Windows 非 UTF-8 控制台可能把中文显示成 `?`；优先使用浏览器或 UTF-8 输出查看中文内容
- `text_cli.py` 的文件工具只允许访问项目目录内文件
