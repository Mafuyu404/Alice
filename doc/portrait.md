# 立绘系统

## 概述

立绘系统包含两个独立组件：

1. **立绘窗口** (`overlay_slideshow.py`) — PySide6 透明窗口，显示立绘图片
2. **立绘控制** (`kokoro/portrait_controller.py`) — LLM 驱动的表情选择 + 子进程管理

## 立绘窗口

`PortraitOverlay` 类创建一个无边框、透明背景的置顶窗口。

### 特性

- 窗口置顶，不干扰其他操作
- 支持透明 PNG 图片叠加
- 鼠标点击穿透模式（`portrait_click_through = true`，适合游戏等场景）
- 拖拽移动位置（点击穿透关闭时）
- 图片随机抖动动画
- HTTP 控制服务器（端口 17352），用于接收切换指令

### HTTP 控制 API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/status` | GET | 返回当前立绘状态（含当前表情名、可用列表） |
| `/set_portrait` | POST | 切换立绘，`{"id": "expression_name"}` |
| `/list_portraits` | GET | 列出所有可用立绘 |
| `/control` | POST | 控制指令：`{"action": "show"|"pause"|"shutdown", "name": "..."}` |
| `/debug` | POST | 发送调试信息叠加显示，`{"data": {...}}` |

### 状态持久化

窗口位置、当前立绘、暂停状态保存在 `portrait_overlay_state.json`（已 gitignore），重启后自动恢复。

### 立绘素材

图片存放在 `img/` 目录，格式为 `{表情}.png`。旁白注释在 `portrait_notes.json` 中定义，供 LLM 选择时参考：

```json
{
  "portraits": [
    {"id": "happy.png", "notes": "开心微笑"},
    {"id": "thinking.png", "notes": "思考/歪头"},
    {"id": "idle.png", "notes": "平静/待机"}
  ]
}
```

## 立绘控制

`kokoro/portrait_controller.py` 管理表情选择和子进程生命周期：

### PortraitOverlayClient

HTTP 客户端，与立绘窗口通信：

| 方法 | 功能 |
|------|------|
| `start()` | 启动立绘窗口子进程，等待就绪 |
| `is_running()` | 健康检查 |
| `show(name)` | 切换立绘 |
| `status()` | 获取当前状态 |
| `send_debug(data)` | 发送调试覆盖信息 |
| `pause()` | 暂停立绘窗口 |
| `shutdown()` | 关闭子进程 |

### PortraitDecisionWorker

后台线程，根据对话内容选择表情：

1. `submit(user_text, assistant_text)` 被调用，标记待决策
2. 检查空闲时间：超过 `portrait_decay_seconds`（默认 60 秒）无对话 → 自动恢复平静表情
3. 在待决策状态下，调用 LLM 分析对话情绪
4. 从 `portrait_notes.json` 的候选列表中选择最匹配的表情 ID
5. 调用 `client.show()` 切换立绘

### 提示词

`prompts.json` 中的 `portrait_selection` 部分：
- `system` — 立绘选择器的系统提示词
- `user_template` — 包含当前立绘、对话内容、候选列表
- `time_info_idle` — 空闲时间信息（用于长时间无对话后的表情选择）
- `time_info_recent` — 对话刚结束的时间信息

## 配置

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0    # 0=使用后端默认
portrait_decay_seconds = 60.0       # 无对话后恢复平静的等待秒数
portrait_debug_overlay = true       # 显示冲动值调试信息
portrait_click_through = false      # 鼠标点击穿透
```

## 启动选项

- `--no-portrait`：禁用立绘叠加层
