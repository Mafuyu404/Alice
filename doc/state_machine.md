# 状态机

## 概述

`kokoro/state_machine.py` 是框架的中央状态管理模块，替代了早期版本中分散的布尔标志和锁。它实现了两级层次状态机：系统级状态描述 Alice 整体在做什么，组件级状态描述各模块内部生命周期。

## 设计动机

早期版本的状态管理依赖多个分散变量：

- `stt_running: bool` — 控制 5 个线程的生死
- `chat_lock: threading.Lock` — 防止并发 LLM 调用
- `busy = tts_engine.is_playing or chat_lock.locked()` — 在 4 处重复判断
- `active_screen_watch_id` + `canceled_screen_watch_ids` — 屏幕监控取消逻辑

这种模式的问题：没有单一真相来源、隐式状态转换、竞态条件风险、错误恢复行为不一致、状态不可观测。

## 架构

### 两级设计

```
SystemStateMachine
  ├── SystemState (系统级 — 干什么)
  │   INITIALIZING → IDLE ⇄ LISTENING / THINKING / SPEAKING / SCREEN_WATCHING
  │   ERROR → (auto-recover) → IDLE
  │   SHUTTING_DOWN (终态)
  │
  └── Component States (组件级 — 模块自己)
      ├── STTState: INACTIVE / LISTENING / PAUSED
      ├── TTSState: IDLE / STREAMING / DRAINING
      ├── PoolState: COLLECTING / REFINING / READY
      ├── PortraitState: INACTIVE / SLIDESHOW / DECIDING / NEUTRAL
      └── ProactiveState: DISABLED / ACCRUING / DECIDING / DEFERRED / EXECUTING
```

### 系统状态说明

| 状态 | 含义 | 允许并发 |
|---|---|---|
| `INITIALIZING` | 加载模型、连接服务 | — |
| `IDLE` | 空闲，等待任何输入 | 屏幕监控、记忆轮询、立绘轮播 |
| `LISTENING` | STT 正在捕获用户语音 | 屏幕监控、记忆轮询 |
| `THINKING` | LLM 正在生成回复 | 立绘决策、命令执行(vision) |
| `SPEAKING` | TTS 正在播放语音 | 立绘决策 |
| `SCREEN_WATCHING` | 被动屏幕分析 | 记忆轮询 |
| `ERROR` | 可恢复错误，1 秒后自动恢复 | — |
| `SHUTTING_DOWN` | 优雅关闭中（终态） | — |

## 事件系统

所有状态转换由事件驱动。16 个事件覆盖全部生命周期：

```python
class SystemEvent(StrEnum):
    # 生命周期
    INIT_DONE / SHUTDOWN / ERROR / FATAL

    # 语音管线
    USER_SPEECH_START / USER_SPEECH_END / STT_REFINED

    # 命令
    COMMAND_DETECTED / COMMAND_COMPLETED

    # LLM
    LLM_START / LLM_DONE

    # TTS
    TTS_START / TTS_DONE

    # 主动语音
    PROACTIVE_TRIGGERED / PROACTIVE_DEFERRED / PROACTIVE_DONE

    # 屏幕 / 记忆
    SCREEN_INTEREST / SCREEN_WATCH_DONE / MEMORY_EVENT
```

## 状态转换表

```
IDLE          + user_speech_start    → LISTENING
IDLE          + proactive_triggered  → THINKING
IDLE          + screen_interest      → SCREEN_WATCHING

LISTENING     + user_speech_end      → IDLE
LISTENING     + stt_refined          → THINKING
LISTENING     + command_detected     → THINKING

THINKING      + llm_done             → SPEAKING
THINKING      + error                → ERROR

SPEAKING      + tts_done             → IDLE
SPEAKING      + user_speech_start    → LISTENING (打断)

SCREEN_WATCHING + screen_watch_done  → IDLE
SCREEN_WATCHING + user_speech_start  → LISTENING

ERROR         + (auto-recover)       → IDLE
ERROR         + fatal                → SHUTTING_DOWN

ANY           + shutdown             → SHUTTING_DOWN
```

## 线程安全

单个 `threading.Lock` 保护所有状态访问。`emit()` 方法是原子的：检查守卫条件 → 更新状态 → 通知观察者，全程持锁。两个线程同时发起冲突事件时，只有一个胜出并完成转换，另一个因找不到匹配的转换而安全返回 `False`。

## 观察者模式

```python
machine.subscribe(lambda old, new, event: print(f"{old} → {new} via {event}"))
```

组件可以订阅状态变化以触发副作用（如进入 THINKING 时通知立绘开始决定表情，退出 SPEAKING 时通知调度器可以 tick）。

## 便捷属性

| 属性 | 含义 |
|---|---|
| `state` | 当前系统状态 |
| `is_busy` | `state in (THINKING, SPEAKING)` — 替代旧版 `busy` |
| `is_idle` | `state == IDLE` |
| `can_accept_speech` | 是否可接受新语音输入 |
| `can_start_conversation` | 是否可开始新一轮对话 |

## 错误恢复

- `emit_error(source)` — 记录错误，进入 ERROR 状态
- 连续 `max_consecutive_errors`（默认 3）次错误升级为 `FATAL` → `SHUTTING_DOWN`
- `error_recovery_worker` 线程轮询 ERROR 状态，1 秒后自动恢复到 IDLE（清理 TTS 等资源）
- `reset_error_count()` — 成功完成一轮对话后重置计数器

## 状态快照

`snapshot()` 返回 `StateSnapshot` 数据类，可序列化为 dict，供调试和立绘 debug overlay 使用：

```python
{
    "system": "THINKING",
    "stt": "LISTENING",
    "tts": "IDLE",
    "pool": "READY",
    "portrait": "DECIDING",
    "proactive": "ACCRUING",
    "error_count": 0,
    "last_transition": "IDLE→THINKING (stt_refined)",
    "uptime": "120s"
}
```

## CLI 集成

`cli.py` 中状态机替代了以下旧机制：

| 旧机制 | 新机制 |
|---|---|
| `stt_running: bool` | `machine.is_shutting_down` |
| `chat_lock` | `machine.is_busy` + `emit()` 原子检查 |
| `busy` 变量 | `machine.is_busy` |
| `active_screen_watch_id` + cancel set | 屏幕结果返回后检查 `machine.can_start_conversation` |
| 各 worker 独立 try/except | 统一 `machine.emit_error()` → 自动恢复 |
