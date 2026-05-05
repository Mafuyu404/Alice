# Web UI

## 概述

`webui.py` 基于 FastAPI 的 Web 界面，提供浏览器中的文字对话体验。不涉及 STT/TTS/立绘等语音功能。

## 启动

```bash
python webui.py
# 可选参数通过 uvicorn 传入：
# python webui.py  # 默认监听 127.0.0.1:8080
```

当前版本 CLI 参数通过 uvicorn 默认值处理（host=127.0.0.1, port=8080）。可通过环境变量 `LLM_BACKEND_CMD` 指定 LLM 后端启动命令。

打开 http://localhost:8080 即可使用。

## 自动启动

WebUI 启动时会自动管理后端进程：

- **LLM 后端**：检测 `LLM_URL` 是否可达，不可达且 `LLM_BACKEND_CMD` 环境变量已设置时，自动启动后端进程
- **KokoroMemo**：当 `memory_backend = "kokoromemo"` 且 `kokoromo_dir` 配置了有效路径时，自动启动 `kokoromemo-server.exe`（监听 127.0.0.1:14514）
- 退出时通过 `atexit` 自动清理所有子进程

## API 端点

### 角色管理

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/characters` | GET | 列出所有角色（返回 `characters.json` 完整内容） |
| `/api/characters/{key}` | GET | 获取单个角色详情（含 key 字段） |
| `/api/characters/{key}` | POST | 创建新角色。body: `CharacterModel`（name, description, personality, background, greeting, example_dialogue） |
| `/api/characters/{key}` | PUT | 更新角色。body 同 POST |
| `/api/characters/{key}` | DELETE | 删除角色 |

### 对话

| 端点 | 方法 | 功能 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容的聊天接口，支持流式/非流式。body: `{"messages": [...], "model": "...", "stream": true, "character_id": "..."}`。自动路由 deepseek 模型、kokoromemo 记忆后端，自动注入记忆上下文和异步存储 |

### 模型管理

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/models` | GET | 返回当前模型和可用模型列表 `{"current": "...", "available": [...]}` |
| `/api/models/switch` | POST | 切换模型。body: `{"model": "model_name"}`。模型不在 `available_models` 中时返回错误 |

### 系统

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/health` | GET | 健康检查。返回记忆后端状态（kokoromemo 健康检查 / mem0 ready / none）、LLM 状态、可用模型数 |
| `/` | GET | 返回内嵌的静态 HTML 聊天页面（`index.html`） |

## 前端

内嵌在 FastAPI 中的静态 HTML 页面，使用 Server-Sent Events 接收流式响应。无额外前端依赖。

## 角色数据模型

```python
class CharacterModel(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    background: str = ""
    greeting: str = ""
    example_dialogue: str = ""
```

注意：`relationship` 和 `proactive_guidance` 字段不在 CharacterModel 中，需直接编辑 `characters.json` 添加。

## 与 CLI 模式对比

| 特性 | WebUI | CLI 语音模式 |
|------|-------|-------------|
| 语音输入 | ✗ | ✓ (sherpa-onnx STT) |
| 语音输出 | ✗ | ✓ (MiniMax/Cartesia TTS) |
| 立绘表情 | ✗ | ✓ |
| 主动搭话 | ✗ | ✓ |
| 屏幕监控 | ✗ | ✓ |
| 用户命令（"看看屏幕"） | ✗ | ✓ |
| 角色管理（CRUD） | ✓ | ✗ |
| 模型切换 | ✓（运行时切换） | 启动时 `--model` 指定 |
| KokoroMemo 管理 | ✓（自动启停） | ✗ |
| LLM 后端管理 | ✓（自动启动） | ✗ |
| 记忆注入 | ✓（mem0 + kokoromemo） | ✓（仅 mem0） |
