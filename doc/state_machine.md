# 状态机

实现文件：`kokoro/state_machine.py`

## 作用

单一日志状态机，作为**系统状态的单一可信源**。所有模块（STT、TTS、调度器、CLI）都通过状态机协调，不各自维护碎片化标志位。

## 状态定义

### SystemState（主状态）

| 状态 | 说明 |
|---|---|
| `INIT` | 启动中 |
| `IDLE` | 空闲，等待用户说话 |
| `LISTENING` | 用户正在说话 |
| `THINKING` | LLM 正在生成回复 |
| `SPEAKING` | TTS 正在播放 |
| `SCREEN_WATCHING` | 空闲时屏幕监控 |
| `ERROR` | 错误，自动恢复 |

### TTSState

| 状态 | 说明 |
|---|---|
| `IDLE` | TTS 空闲 |
| `STREAMING` | TTS 正在流式播放 |
| `DRAINING` | LLM 已结束，TTS 缓冲区还在播 |

### ProactiveState

| 状态 | 说明 |
|---|---|
| `DISABLED` | 主动搭话关闭 |
| `ACCRUING` | 可积累主动搭话计划 |
| `EXECUTING` | 正在执行主动搭话 |

### STTState

| 状态 | 说明 |
|---|---|
| `LISTENING` | 正在识别 |
| `PROCESSING` | 正在处理识别结果 |

### PortraitState

| 状态 | 说明 |
|---|---|
| `HIDDEN` | 隐藏 |
| `SLIDESHOW` | 立绘轮播显示 |

## 事件驱动

状态通过 `emit(SystemEvent)` 切换：

```text
SystemEvent.INIT_DONE      → INIT → IDLE
SystemEvent.USER_SPEECH_START → IDLE → LISTENING
SystemEvent.STT_REFINED    → LISTENING → THINKING
SystemEvent.PROACTIVE_TRIGGERED → 检查是否可从空闲进入主动说话
SystemEvent.LLM_DONE       → THINKING → SPEAKING
SystemEvent.TTS_DONE       → SPEAKING → IDLE
SystemEvent.SHUTDOWN       → 任何状态 → 关机
SystemEvent.ERROR          → 任何状态 → ERROR
```

## 守卫

状态机提供守卫方法供其他模块查询状态：

```python
machine.is_idle          # 是否空闲
machine.is_busy          # 是否在处理任何事情
machine.can_start_conversation   # 是否可以主动开口
```

## 订阅者模式

外部模块通过 `subscribe(callback)` 监听状态变化：

```python
def on_state_change(old, new, event):
    if new == SystemState.ERROR:
        print(f"系统错误: {event}")

machine.subscribe(on_state_change)
```

cli.py 中使用订阅者记录状态变化日志。

## 错误恢复

进入 `ERROR` 状态后，`error_recovery_worker` 每秒检查一次，调用 `recover_from_error()` 尝试回到 `IDLE`。

错误计数器累计（`emit_error()`），可限制连续错误后的重试行为。

## 设计要点

- 状态切换是原子的（锁保护）
- 不允许非法跳转（如从 LISTENING 直接到 INIT）
- ProactiveState 与 SystemState 解耦——系统可以是 IDLE 且 PROACTIVE_DISABLED
