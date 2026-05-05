# 主动搭话调度器

## 概述

`kokoro/proactive.py` 实现了一个冲动值（drive）驱动的调度器，让角色在空闲时根据多种因素主动发起对话。调度器通过计算四类行为的冲动值，选择最合适的行为触发。

## 行为类型

| 行为 | 触发场景 | 冲动值变化 |
|------|---------|-----------|
| `IDLE` | 用户长时间没说话 | 空闲时按 rate 增长，活跃时衰减 |
| `RECENT` | 对话刚结束不久 | 对话结束后按 decay_rate 衰减（有延迟） |
| `MEM` | 记忆事件触发 | 由 `add_memory_interest()` 注入分值 |
| `SCREEN` | 屏幕内容有趣 | 由 `add_screen_interest()` 注入，空闲时按 decay_rate 衰减 |

## 核心概念

### 冲动值 (Desire)

每种行为有独立冲动值（0 ~ 100+ 浮点数）：
- **增长**：IDLE/MEM 按 rate 每秒增长
- **衰减**：RECENT/SCREEN 按 decay_rate 每秒衰减
- **注入**：SCREEN 通过屏幕兴趣分析注入，MEM 通过记忆事件注入
- **全局倍率**：`drive_rate` 乘数作用于所有增/减速

### 干扰值 (Disturbance)

衡量对用户的打扰程度：
- 对话刚结束时最高，随时间平稳衰减
- 超过行为 `max_disturbance` 阈值的行为不被触发

### 多样性窗口 (Diversity Window)

`diversity_window_seconds` 内不会重复触发同一类行为。当第一候选被多样性过滤时，有 `secondary_pick_chance` 概率选择第二候选。

## 决策流程

```
tick() 每 tick_seconds 调用一次:
  │
  ├─ 1. _update_desires() — 更新所有行为的冲动值
  │
  ├─ 2. 检查 TTS/STT 是否繁忙
  │    - 繁忙 → 延期，扣除 defer_penalty，返回
  │
  ├─ 3. _candidates() — 过滤可触发的行为
  │    - 冲动值 ≥ active_threshold
  │    - 干扰值 ≤ max_disturbance
  │    - 不在冷却期内
  │    - 行为已启用
  │
  ├─ 4. _select_with_diversity() — 多样性选择
  │    - 按冲动值排序 → 多样性窗口检查 → 概率选第二候选
  │
  └─ 5. 触发 → 注入上下文标签 → 构建主动对话消息 → ChatSession
```

## 提示词注入

触发主动搭话时，调度器在消息列表中注入：

1. **`proactive.{behavior}`** — 对应行为的角色提示词（如 `proactive.screen`）
2. **`proactive.screen_context_label` / `proactive.mem_context_label`** — 注入 `{context}`（屏幕内容或记忆内容）
3. **`proactive.trigger_system`** — 提醒 AI 这是系统触发的主动行为
4. **`proactive.trigger_guidance_label`** — 注入角色的 `proactive_guidance`（如果存在）

## 配置

### 全局

```toml
[proactive]
enabled = true
tick_seconds = 3.0           # 主循环间隔
drive_rate = 4.0             # 全局速率倍率
diversity_window_seconds = 60.0
secondary_pick_chance = 0.2
```

各行为子配置见 `config.toml` 的 `[proactive.idle]` `[proactive.recent]` `[proactive.mem]` `[proactive.screen]` 小节。

## 外部接口

调度器通过以下方法与外部模块交互：

| 方法 | 调用方 | 功能 |
|------|--------|------|
| `record_user_activity()` | `on_refined` 回调 | 用户说话时重置空闲计时器 |
| `record_conversation_end(text, reply)` | `on_refined` 回调 | 记录对话结束，触发 RECENT 联动 |
| `record_tts_end()` | 主线程 | TTS 播放完毕 |
| `reset_all()` | `on_refined` 回调 | 用户主动说话时重置所有冲动值为 0 |
| `add_screen_interest(score, context)` | screen_watch 线程 | 注入屏幕兴趣 |
| `add_memory_interest(score, context)` | memory_events 线程 | 注入记忆事件兴趣 |
| `tick(busy=False)` | 主循环 | 每次调用执行一次决策周期 |
| `snapshot()` | 调试 | 返回当前冲动值与状态快照 |
