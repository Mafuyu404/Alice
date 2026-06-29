# 对话调度器

实现文件：`kokoro/dialogue_orchestrator.py`

`DialogueOrchestrator` 是当前一对一语音/文本场景的快速说话路径。它不是生命周期中心；中心是 [生命周期架构](lifecycle.md) 中的“内在叙事流 → 行动能力 → 信息循环”。

## 职责

当用户语音或文本进入时，它快速判断是否需要立刻采取 `say` 类行动：

| 动作 | 含义 |
|---|---|
| `silence` | 听见但不回应，记录为自身行动 |
| `observe` | 记录为观察，不打断当前状态 |
| `backchannel` | 很短的轻回应 |
| `speak` | 生成正常回复 |
| `schedule` | 稍后再说 |
| `cancel_plan` | 取消待执行延迟发言 |

这些动作本质上都应视为行动能力：`speak/backchannel` 是 `say`，`silence/observe` 是 `wait` 或 `stay_silent`，`schedule` 是延迟行动。

## 数据流

```text
用户语音/文本事件
  ├─ DialogueOrchestrator.decide() 低延迟判断
  │    ├─ say / backchannel → 立即输出
  │    └─ silence / observe / schedule → 记录行动
  └─ InnerStreamLoop 异步吸收同一事件和行动结果
```

如果回复过程中启动搜索、看屏幕或长任务，结果不应直接拼接到旧回复；它应作为新的行动结果事件回到 `InputEventBus`，再由内在叙事流和行动层决定下一步。

## 与后续重构的关系

长期目标是把对话调度器收束到统一行动选择层：

```text
当前：DialogueOrchestrator 单独判断说不说
目标：LifeLoop/ActionPolicy 统一选择 say / wait / observe_screen / search_web / write_memory ...
```

在重构完成前，`DialogueOrchestrator` 保留为实时语音体验的快速路径，但它的所有输出都必须回写为事件，让内在叙事流能吸收“我刚刚说了/没说/稍后再说”的经历。
