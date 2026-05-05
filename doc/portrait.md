# 立绘系统

## 概述

立绘系统包含两个独立组件和两个配置文件：

1. **立绘窗口** (`overlay_slideshow.py`) — PySide6 透明窗口，显示立绘图片，内置 HTTP 控制服务
2. **立绘控制** (`kokoro/portrait_controller.py`) — LLM 驱动的表情选择 + 子进程管理
3. **`portrait_notes.json`** — 立绘注释（供 LLM 选图用）
4. **`portrait_map.json`** — 立绘素材映射（供 overlay 轮播用，位于 `img/` 目录）

## 立绘窗口

`PortraitOverlay` 类创建一个无边框、透明背景的置顶窗口。

### 特性

- 窗口置顶，不干扰其他操作
- 支持透明 PNG 图片叠加
- 鼠标点击穿透模式（`portrait_click_through = true`，适合游戏等场景）
- 拖拽移动位置（点击穿透关闭时）
- 鼠标滚轮缩放（0.2x ~ 4.0x，步进 0.1x）
- 图片轮播（暂停/播放控制，默认 2 秒间隔）
- 系统托盘图标（右键菜单：切换穿透、播放/暂停、退出）
- HTTP 控制服务器（端口默认 17352），用于接收切换指令
- 调试叠加层（显示冲动值状态）
- DWM 原生窗口修复（禁用圆角、强制 popup 样式）

### 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| `F8` | 切换鼠标点击穿透 |
| `Space` | 播放/暂停轮播 |
| `→` | 下一张立绘（手动暂停） |
| `←` | 上一张立绘（手动暂停） |
| `Esc` | 退出立绘窗口 |

### HTTP 控制 API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查，返回 `{"ok": true}` |
| `/status` | GET | 返回当前立绘状态（含当前 asset、所有 series、素材总数） |
| `/portraits` | GET | 列出立绘。支持查询参数过滤：`?series=&emotion=&pose=&eyes=&mouth=` |
| `/control` | POST | 控制指令。body: `{"action": "show"|"pause"|"play"|"click_through"|"shutdown", ...}` |
| `/debug` | POST | 发送调试信息叠加显示。body: `{"data": {...}}` |

#### /control 的 action 参数

| action | 额外参数 | 功能 |
|--------|---------|------|
| `show` | `name` 或 `random` + 过滤字段 | 切换到指定立绘或随机选择 |
| `pause` | — | 暂停轮播 |
| `play` | — | 恢复轮播 |
| `click_through` | `enabled` (bool) | 设置鼠标穿透 |
| `shutdown` | — | 关闭立绘窗口 |

### 状态持久化

窗口位置（x, y）、缩放比例（scale_factor）保存在 `portrait_overlay_state.json`（已 gitignore），重启后自动恢复。

### 立绘素材

- 图片存放在 `img/` 目录
- `portrait_map.json` 定义素材映射（含 series、emotion、pose、eyes、mouth 等标签字段）
- `portrait_notes.json` 定义供 LLM 选择时参考的注释（id + notes），格式：`{"portraits": [{"id": "happy.png", "notes": "开心微笑"}, ...]}`

## 立绘控制

`kokoro/portrait_controller.py` 管理表情选择和子进程生命周期：

### PortraitOverlayClient

HTTP 客户端，与立绘窗口通信：

| 方法 | 功能 |
|------|------|
| `start()` | 启动立绘窗口子进程（`python overlay_slideshow.py`），等待就绪（8s 超时） |
| `is_running()` | 健康检查（`GET /health`） |
| `wait_until_ready(timeout=8.0)` | 轮询等待窗口就绪 |
| `show(name)` | 切换立绘（`POST /control` action=show） |
| `status()` | 获取当前状态（`GET /status`） |
| `send_debug(data)` | 发送调试覆盖信息（`POST /debug`） |
| `pause()` | 暂停轮播 |
| `shutdown()` | 关闭子进程（`POST /control` action=shutdown），超时则 terminate |

### PortraitDecisionWorker

后台线程，根据对话内容选择表情：

1. `submit(user_text, assistant_text)` 被调用，标记待决策
2. 检查空闲时间：超过 `portrait_decay_seconds`（默认 60 秒）无对话 → 自动恢复 `neutral` 表情
3. 在待决策状态下，调用 LLM 分析对话情绪
4. 从 `portrait_notes.json` 的候选列表中选择最匹配的表情 ID
5. 调用 `client.show()` 切换立绘

### 提示词

`prompts.json` 中的 `portrait_selection` 部分：
- `system` — 立绘选择器的系统提示词
- `user_template` — 包含当前立绘、对话内容、候选列表。参数：`{current_id}` `{user_text}` `{assistant_text}` `{time_info}` `{catalog}`
- `time_info_idle` — 空闲时间信息。参数：`{seconds}`
- `time_info_recent` — 对话刚结束的时间信息

### 创建入口

`create_controller(model)` 工厂函数：读取配置中的 host/port，创建 `PortraitOverlayClient` 并启动子进程，然后创建 `PortraitDecisionWorker`。返回 `(client, worker)` 元组。

## 配置

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0    # 0=使用后端默认（约 2 秒）
portrait_decay_seconds = 60.0       # 无对话后恢复平静的等待秒数
portrait_debug_overlay = true       # 显示冲动值调试信息
portrait_click_through = false      # 鼠标点击穿透
```

## 启动选项

- `--no-portrait`：禁用立绘叠加层
