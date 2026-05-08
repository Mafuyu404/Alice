# Impulse 主动搭话

`kokoro/impulse.py` 是当前主动搭话实现。它不再使用旧的 desire 调度器，而是在对话结束后结合屏幕内容、最近对话、摘要和记忆，生成短期计划表，再按计划在空闲时触发一句自然发言。

主要配置位于 `config.toml` 的 `[impulse]`：

```toml
[impulse]
enabled = true
planning_model = "deepseek-v4-flash"
max_plans = 5
min_plans = 1
empty_plan_retry_seconds = 30.0
max_consecutive_impulse = 3
log_plan_table = true
memory_events_enabled = true
memory_check_interval = 300.0
```

屏幕监控仍由 `[screen_watch]` 控制；它只负责把有价值的屏幕观察写入会话上下文，不再向旧调度器注入 SCREEN desire。
