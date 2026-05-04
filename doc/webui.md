# Web UI

## 概述

`webui.py` 基于 FastAPI 的 Web 界面，提供浏览器中的文字对话体验。

## 启动

```bash
python webui.py
# 可选参数：
python webui.py --model qwen2.5:7b --character alice --port 8080
```

打开 http://localhost:8080 即可使用。

## 可用的 CLI 参数

| 参数 | 作用 |
|------|------|
| `--character` | 指定角色 ID |
| `--model` | 指定对话模型 |
| `--port` | 指定端口（默认 8080） |
| `--host` | 指定绑定地址（默认 127.0.0.1） |

## API 端点

所有 API 通过 SSE 或 REST JSON 提供。

### 对话

| 端点 | 方法 | 功能 |
|------|------|------|
| `/chat` | POST | 发送消息，SSE 返回流式回复 |
| `/history` | GET | 获取当前对话历史 |

### 角色管理

| 端点 | 方法 | 功能 |
|------|------|------|
| `/characters` | GET | 列出所有角色 |
| `/characters` | PUT | 更新角色数据 |
| `/characters` | POST | 添加新角色 |
| `/characters/{char_id}` | DELETE | 删除角色 |

### 系统

| 端点 | 方法 | 功能 |
|------|------|------|
| `/models` | GET | 获取可用模型列表 |
| `/current_model` | GET | 获取当前使用的模型 |
| `/switch_model` | POST | 切换模型，body: `{"model": "model_name"}` |

## 前端

内嵌在 FastAPI 中的静态 HTML 页面，使用 Server-Sent Events 接收流式响应。无额外前端依赖。

## 与 CLI 模式的对比

| 特性 | WebUI | CLI 语音模式 |
|------|-------|-------------|
| 语音输入 | ✗ | ✓ (STT) |
| 语音输出 | ✗ | ✓ (TTS) |
| 立绘 | ✗ | ✓ |
| 主动搭话 | ✗ | ✓ |
| 屏幕监控 | ✗ | ✓ |
| 角色管理 | ✓ | ✗ |
| 模型切换 | ✓ | 启动时指定 |
