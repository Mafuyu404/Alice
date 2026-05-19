# 主动对话

主动对话由统一的单角色调度器负责：

- `kokoro/dialogue_orchestrator.py`

## 作用

在空闲时读取候选上下文，并由 DialogueOrchestrator 判断角色是否自然开口。

候选上下文包括：

- 屏幕兴趣度缓存
- Edge 网页缓存
- 日期/长期记忆的低频提示
- 直播弹幕上下文

## 配置

```toml
[proactive]
enabled = true
planning_model = ""

[proactive_memory]
memory_events_enabled = true
memory_check_interval = 300.0
```

## 原则

- 没有独立的旧主动规划器；主动、延迟、沉默都走 DialogueOrchestrator。
- 屏幕、网页、记忆和弹幕只是候选材料，不自动触发台词。
- 用户开口时会取消待执行的延迟对话计划。
- 角色是否开口、是否展开、是否稍后再说，由 planner 根据角色、场景、认知和情绪共同判断。
