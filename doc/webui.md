# Web UI

## 概述

`webui.py` 基于 FastAPI 的 Web 界面，提供浏览器中的文字对话体验。不涉及 STT/TTS/立绘等语音功能。

## 启动

```bash
python webui.py
# 可选参数：
python webui.py --model qwen2.5:7b --character alice --port 8080
```

打开 http://localhost:8080 即可使用。

## CLI 参数

| 参数 | 作用 |
|------|------|
| `--character` | 指定角色 ID（默认从 config.toml 读取） |
| `--model` | 指定对话模型 |
| `--port` | 端口（默认 8080） |
| `--host` | 绑定地址（默认 127.0.0.1） |

## API 端点

### 对话

| 端点 | 方法 | 功能 |
|------|------|------|
| `/chat` | POST | 发送消息，SSE 返回流式回复。body: `{"message": "你好"}` |
| `/history` | GET | 获取当前对话历史 |

### 角色管理

| 端点 | 方法 | 功能 |
|------|------|------|
| `/characters` | GET | 列出所有角色 |
| `/characters` | PUT | 更新角色数据。body: `{char_id: {...}}` |
| `/characters` | POST | 添加新角色。body: `{char_id: {...}}` |
| `/characters/{char_id}` | DELETE | 删除角色 |

### 系统

| 端点 | 方法 | 功能 |
|------|------|------|
| `/models` | GET | 返回 `available_models` 列表 |
| `/current_model` | GET | 返回当前使用的模型名 |
| `/switch_model` | POST | 切换模型。body: `{"model": "model_name"}` |

## 前端

内嵌在 FastAPI 中的静态 HTML 页面，使用 Server-Sent Events 接收流式响应。无额外前端依赖。

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
| 模型切换 | ✓ | 启动时 `--model` 指定 |
