# 架构概览

当前主线是生命周期架构：**信息事件 → 内在叙事流 → 行动能力 → 行动结果 → 信息事件**。详见 [生命周期架构](lifecycle.md)。

## 运行时分层

```text
入口层
  cli.py / text_cli.py / run_multi.py
      ↓
事件层
  kokoro/core/input_events.py
  统一承载语音、文本、QQ、屏幕、网页、时间、行动结果
      ↓
内在叙事层
  kokoro/core/inner_stream.py
  维护当前心境、注意、悬挂线索、行动倾向和边界
      ↓
行动选择层
  autonomous_step.py / dialogue_orchestrator.py / agent_guard.py
  根据内在叙事流、近期事件和可用能力选择下一步行动
      ↓
行动执行层
  kokoro/action/runtime.py / tool_registry.py / tool_handlers.py / TTS / QQ / VTS / memory
  执行说话、搜索、观察、记忆、动作、等待等能力
      ↓
经验回写
  record_input_event() / record_self_action() / memory_events
  将行动开始、结果、失败、沉默和等待写回事件流
```

## 数据流

```text
外部输入或时间心跳
  → InputEventBus.publish()
  → InnerStreamLoop 合并并更新内在叙事流
  → 行动选择层输出 ActionBatch
  → 行动执行层并行执行批次内行动
  → 行动结果按 cycle_id / action_id / causality_id 回写
  → 短窗口合并结果后再次进入内在叙事流
```

语音对话为了低延迟保留快速路径：

```text
用户语音
  ├─ 快速判断是否立刻 say / backchannel / wait
  └─ 同时进入 InnerStreamLoop，作为经历异步吸收
```

快速路径只是实时性优化，不改变主线：所有说话、沉默和工具结果都必须回写为事件。

## 模块职责

| 模块 | 职责 |
|---|---|
| `kokoro/core/input_events.py` | 统一事件结构、事件总线、事件格式化 |
| `kokoro/core/inner_stream.py` | 内在叙事流存取、事件合并、节奏更新 |
| `kokoro/action/model.py` | `Action` / `ActionBatch` 批次模型和追踪 ID |
| `kokoro/action/runtime.py` | 行动执行、并行启动、结果合并和事件回写 |
| `kokoro/action/autonomous_step.py` | 以内在叙事流为核心的自主行动决策雏形 |
| `kokoro/action/dialogue_orchestrator.py` | 当前一对一语音/文本快速说话路径 |
| `kokoro/action/agent_guard.py` / `kokoro/action/agent_loop.py` | 当前工具路由和兼容式 function-call 循环 |
| `kokoro/action/tool_registry.py` / `kokoro/action/tool_handlers.py` | 可执行能力注册和调用 |
| `kokoro/core/chat_session.py` | 会话历史、摘要、记忆/认知/情绪维护和事件回写 |
| `kokoro/core/memory_events.py` / `kokoro/core/memory.py` | 经历沉淀和长期检索 |
| `kokoro/core/cognition.py` / `kokoro/core/emotion.py` | 稳定认知和短中期情绪状态 |

## 设计决策

| 决策 | 理由 |
|---|---|
| 内在叙事流是中心 | 没有人说话时系统仍能维持注意、活动和未完成感 |
| 所有行为都能力化 | 说话、沉默、搜索、记忆、表情和等待可以被同一套行动层处理 |
| 行动选择输出批次 | 同一轮内在叙事流可能自然触发多个并行动作 |
| 结果按因果链合并 | 避免多个工具同时完成时连续触发内在叙事流 |
| 行动结果回写事件 | 让工具调用、失败、等待和沉默都能沉淀为经历 |
| 慢行动异步执行 | 识图、搜索、长任务不阻塞当前表达 |
| Python 不解析 inner stream 栏目 | 避免把心理文本硬编码成规则 |
| 语音保留快速路径 | 实时对话需要低延迟，但结果仍进入事件循环 |
