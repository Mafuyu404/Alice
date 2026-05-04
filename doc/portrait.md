# 立绘叠加层

## 概述

立绘系统包含两个独立组件：

1. **立绘窗口** (`overlay_slideshow.py`) — PySide6 透明窗口，显示立绘图片
2. **立绘控制** (`kokoro/portrait_controller.py`) — LLM 驱动的表情选择逻辑

## 立绘窗口

`PortraitOverlay` 类创建一个无边框、透明背景的窗口：

### 特性

- 窗口置顶，不干扰其他操作
- 支持透明 PNG 图片叠加
- 鼠标点击穿透模式（可配置，适合游戏等场景）
- 拖拽移动位置
- 图片随机抖动动画（可选）
- HTTP 控制端口（默认 17352），用于切换立绘

### HTTP 控制 API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/set_portrait` | POST | 切换立绘，JSON body: `{"id": "expression_name"}` |
| `/get_portrait` | GET | 获取当前立绘状态 |
| `/list_portraits` | GET | 列出可用立绘 |

### 状态持久化

窗口位置和当前立绘保存在 `portrait_overlay_state.json`（已 gitignore），重启后恢复。

### 立绘素材

图片存放在 `img/` 目录，格式为 `{表情}.png`（如 `happy.png`、`idle.png`、`thinking.png`）。

## 立绘控制

`kokoro/portrait_controller.py` 管理表情选择和切换逻辑：

### PortraitOverlayClient

HTTP 客户端，与立绘窗口通信：

| 方法 | 功能 |
|------|------|
| `set_portrait(expression_id)` | 切换立绘 |
| `get_current()` | 获取当前表情 |
| `list_portraits()` | 列出所有可用表情 |
| `ensure_alive()` | 确保立绘窗口在运行 |

### PortraitDecisionWorker

后台线程，定期调用 LLM 根据对话上下文选择立绘表情：

1. 收集最近的对话历史
2. 查看用户最近的语音状态（说话/沉默）
3. 调用 LLM 判断当前情绪和场景
4. 从可用表情列表中选择最匹配的
5. 触发切换

### 提示词

`prompts.json` 中的 `portrait_selection` 部分包含：
- `time_info_idle`：空闲时的时间信息注入
- `time_info_recent`：对话刚结束时的沉浸式时间信息注入

### 表情决策

决策基于：
- 对话情绪（开心、悲伤、思考等）
- 用户活跃度
- 当前场景（对话中 / 空闲 / 主动搭话）

### 表情衰减

`portrait_decay_seconds = 60.0`：对话结束后 60 秒无语音，立绘自动恢复为平静表情。

## 配置

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0    # 0=使用后端默认
portrait_decay_seconds = 60.0
portrait_debug_overlay = true
portrait_click_through = false
```

## 启动选项

- `--no-portrait`：禁用立绘叠加层
