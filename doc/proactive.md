# 主动搭话调度器

## 概述

`kokoro/proactive.py` 实现了一个冲动值（drive）驱动的调度器，让角色在空闲时根据多种因素主动发起对话。调度器通过计算不同类型行为的"冲动值"，选择最合适的行为触发主动搭话。

## 行为类型

| 行为 | 触发条件 | 冲动值变化 |
|------|---------|-----------|
| `IDLE` | 长时间无人说话 | 空闲时持续增长，用户活跃时缓慢衰减 |
| `RECENT` | 刚刚结束对话 | 对话结束后逐渐衰减 |
| `MEM` | 记忆事件触发 | 通过 `add_memory_interest()` 注入 |
| `SCREEN` | 屏幕内容分析 | 通过 `add_screen_interest()` 注入，空闲时衰减 |

## 核心概念

### 冲动值 (Desire)

每种行为有独立的冲动值（0-100 浮点数），决定其触发的紧迫程度：

- **增长**：`IDLE` 和 `MEM` 按 rate 每秒增长
- **衰减**：`RECENT` 和 `SCREEN` 按 decay_rate 每秒衰减
- **注入**：`SCREEN` 通过屏幕兴趣分析注入，`MEM` 通过记忆事件注入

### 干扰值 (Disturbance)

衡量对用户的打扰程度：
- 刚结束对话时干扰值最高
- 随时间平稳衰减
- 超过行为 `max_disturbance` 阈值的行为不被选择

### 多样性窗口 (Diversity Window)

`diversity_window_seconds` 内不会重复触发同一类行为。当第一选择因多样性被过滤时，有 `secondary_pick_chance` 的概率选择第二高冲动值的行为。

## 决策流程

```
tick() 每 3 秒调用一次:
  │
  ├─ 1. _update_desires() — 更新所有行为的冲动值
  │    - IDLE: 用户空闲时增长，繁忙时衰减
  │    - RECENT: 对话结束后延迟衰减
  │    - MEM: 按固定速率增长
  │    - SCREEN: 按固定速率衰减
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
  │    - 按冲动值排序
  │    - 检查多样性窗口
  │    - 概率选择第二候选
  │
  └─ 5. 触发 → 构建主动对话消息 → 送入 ChatSession
```

## 配置参考

### 全局设置

```toml
[proactive]
enabled = true
tick_seconds = 3.0
drive_rate = 4.0           # 全局速率倍率
diversity_window_seconds = 60.0
secondary_pick_chance = 0.2
```

### IDLE 行为

```toml
[proactive.idle]
enabled = false            # 默认关闭
rate = 0.08
active_threshold = 60.0
cooldown_seconds = 30.0
defer_penalty = 20.0
max_disturbance = 35.0
weight = 1.0
user_idle_bonus_after_seconds = 120.0  # 用户空闲 2 分钟后加速
```

### RECENT 行为

```toml
[proactive.recent]
enabled = true
decay_rate = 2.0
decay_delay_seconds = 30.0   # 对话结束后 30 秒才开始衰减
active_threshold = 70.0
cooldown_seconds = 30.0
defer_penalty = 20.0
max_disturbance = 45.0
weight = 1.0
bonus_window_seconds = 120.0  # 对话结束后 2 分钟内给 IDLE 加速
```

### MEM 行为

```toml
[proactive.mem]
enabled = true
rate = 0.06
active_threshold = 70.0
cooldown_seconds = 30.0
defer_penalty = 20.0
max_disturbance = 25.0
weight = 1.0
```

### SCREEN 行为

```toml
[proactive.screen]
enabled = true
decay_rate = 5.0
active_threshold = 70.0
cooldown_seconds = 30.0
defer_penalty = 20.0
max_disturbance = 50.0
weight = 1.0
post_conversation_decay = 30.0  # 对话结束后扣除 30 点
```

## 外部接口

调度器通过以下方法与外部模块交互：

- `record_user_activity()` — 用户说话时调用，重置空闲计时器
- `record_conversation_end()` — 对话结束时调用，触发 RECENT 联动
- `add_screen_interest(score)` — `screen_watch` 发现有趣内容时调用
- `add_memory_interest(score)` — `memory_events` 发现记忆事件时调用
