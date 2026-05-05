# 主动搭话调度器

## 概述

`kokoro/proactive.py` 实现了一个冲动值（desire）驱动的调度器，让角色在空闲时根据多种因素主动发起对话。调度器通过计算四类行为的冲动值，结合干扰值过滤和多样性窗口，选择最合适的行为触发。

## 行为类型

| 行为 | 触发场景 | 冲动值变化 |
|------|---------|-----------|
| `IDLE` | 用户长时间没说话 | 空闲时按 rate 增长，活跃时被 `reset_all()` 清零 |
| `RECENT` | 对话刚结束不久 | 对话结束时由 `_conversation_quality()` 注入初始值，延迟后按 decay_rate 衰减 |
| `MEM` | 记忆事件触发 | 由 `add_memory_interest()` 注入分值，按 rate 缓慢增长 |
| `SCREEN` | 屏幕内容有趣 | 由 `add_screen_interest()` 注入（>50 分才注入），按 decay_rate 衰减 |

## 核心概念

### 冲动值 (Desire)

每种行为有独立冲动值（0 ~ 100 浮点数）：
- **增长**：IDLE/MEM 按 `rate × weight × dt × drive_rate` 每秒增长
- **衰减**：RECENT/SCREEN 按 `decay_rate × dt` 每秒衰减
- **注入**：SCREEN 通过 `add_screen_interest()` 注入（取 max），MEM 通过 `add_memory_interest()` 累加注入
- **全局倍率**：`drive_rate` 乘数作用于所有增/减速
- **IDLE 加成**：用户空闲超过 `user_idle_bonus_after_seconds` 后，IDLE 获得 3× 额外增速；对话刚结束且在 `bonus_window_seconds` 内，IDLE 获得 2× 额外增速
- **对话后 SCREEN 衰减**：`record_conversation_end()` 时 SCREEN 冲动值扣除 `post_conversation_decay`

### 干扰值 (Disturbance)

衡量对用户的打扰程度，用于过滤候选行为：
- 用户活跃（距上次活动 < 10s）→ 干扰值 = 30
- 用户半活跃（< 60s）→ 干扰值 = 20
- 用户空闲（≥ 60s）→ 干扰值 = 10
- `quiet_until` 生效期间（用户反馈消极后）→ 干扰值 = 100（强制阻止所有行为 10 分钟）
- 每个行为有 `max_disturbance` 阈值，超过则不被触发

### 多样性窗口 (Diversity Window)

`diversity_window_seconds`（默认 60s）内不会重复触发同一类行为。当第一候选被多样性过滤时，有 `secondary_pick_chance`（默认 0.2）概率选择第二候选。

### 延期机制

当选中行为但 TTS/STT 正忙时（`busy=True`），决策被暂存。下次 tick 不再忙时，以扣除 `defer_penalty` 后的冲动值重新评估，仍达阈值则触发，否则丢弃。

### RECENT 阻止

`record_tts_end()` 后设置 `recent_blocked_until`，阻止 RECENT 在 TTS 刚结束的一个 tick 内触发，避免角色在用户还在消化回复时立刻继续说话。

## 决策流程

```
tick(busy=False) 每次调用:
  │
  ├─ 0. 检查上次延期的决策
  │    - 不再忙 → 扣除 defer_penalty → 达标则触发
  │    - 仍忙 → 继续等待
  │
  ├─ 1. _candidates() — 过滤可触发行为
  │    - 冲动值 ≥ active_threshold
  │    - 干扰值 ≤ max_disturbance
  │    - 不在冷却期内（cooldown_until）
  │    - 行为已启用
  │    - RECENT 不在 recent_blocked_until 内
  │
  ├─ 2. _select_with_diversity() — 多样性选择
  │    - 过滤 diversity_window 内已触发的行为
  │    - 按冲动值排序 → 概率选第二候选
  │
  ├─ 3. 当前 busy → 暂存决策（deferred），返回 None
  │
  ├─ 4. _update_desires() — 更新冲动值（触发后才更新，无触发也更新）
  │
  └─ 5. 触发 → 清零冲动值 → 设置冷却 → 记录历史 → 返回 ProactiveDecision
```

## 提示词注入

触发主动搭话时，CLI 在消息列表中注入：

1. 角色系统提示词（`character_system.template` + `expression_calibration`）
2. `proactive.trigger_system` — 提醒 AI 这是系统触发的主动行为
3. `proactive.trigger_guidance_label` — 注入角色的 `proactive_guidance`（如果存在）
4. `proactive.{behavior}` — 对应行为的角色提示词（如 `proactive.screen`）
5. `proactive.screen_context_label` / `proactive.mem_context_label` — 注入 `{context}`
6. 最近屏幕观察记录 + 记忆上下文

## 外部接口

| 方法 | 调用方 | 功能 |
|------|--------|------|
| `record_user_activity()` | `on_refined` 回调 | 用户说话时更新 `last_user_activity` |
| `record_conversation_end(text, reply)` | `on_refined` 回调 | 记录对话结束，设置 RECENT 初始冲动值，扣除 SCREEN |
| `record_tts_end()` | 主线程 | TTS 播放完毕，设置 `recent_blocked_until` |
| `reset_all()` | `on_refined` 回调 | 用户主动说话时重置所有冲动值为 0 |
| `add_screen_interest(score, context)` | screen_watch 线程 | 注入屏幕兴趣（>50 分才更新） |
| `add_memory_interest(score, context)` | memory_events 线程 | 注入记忆事件兴趣（累加） |
| `apply_feedback(behavior, positive)` | 外部反馈 | 调整行为权重 ±5%/±20%，消极反馈设 `quiet_until` 10 分钟 |
| `tick(busy=False)` | 主循环 | 每次调用执行一次决策周期 |
| `snapshot()` | 调试 | 返回当前冲动值、干扰值、阈值、候选列表 |

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

### 各行为子配置

所有行为共享以下配置结构：

| 参数 | IDLE 默认 | RECENT 默认 | MEM 默认 | SCREEN 默认 | 说明 |
|------|-----------|-------------|----------|-------------|------|
| `enabled` | `false` | `true` | `true` | `true` | 行为开关 |
| `active_threshold` | `60.0` | `70.0` | `70.0` | `70.0` | 触发阈值 |
| `cooldown_seconds` | `30.0` | `30.0` | `30.0` | `30.0` | 冷却时间 |
| `defer_penalty` | `20.0` | `20.0` | `20.0` | `20.0` | 延期扣减值 |
| `max_disturbance` | `35.0` | `45.0` | `25.0` | `50.0` | 最大干扰容忍 |
| `weight` | `1.0` | `1.0` | `1.0` | `1.0` | 权重倍率 |

IDLE 特有：
- `rate = 0.08` — 每秒增长率
- `user_idle_bonus_after_seconds = 120.0` — 空闲多久后加速

RECENT 特有：
- `decay_rate = 2.0` — 每秒衰减率
- `decay_delay_seconds = 30.0` — 衰减延迟
- `bonus_window_seconds = 120.0` — IDLE 加成窗口

MEM 特有：
- `rate = 0.1` — 每秒增长率

SCREEN 特有：
- `decay_rate = 5.0` — 每秒衰减率（屏幕兴趣衰减最快）
- `post_conversation_decay = 30.0` — 对话后扣减值

详见 `config.toml` 各子节的完整注释。
