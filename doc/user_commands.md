# 行动能力与工具

实现文件：

- `kokoro/tool_registry.py`
- `kokoro/tool_handlers.py`
- `kokoro/agent_guard.py`
- `kokoro/agent_loop.py`
- 以及 TTS、QQ、VTS、memory 等输出/维护模块

在生命周期架构中，“工具”应理解为广义行动能力。所有可采取行为都应能力化，包括说话、沉默、搜索、观察、写记忆、更新认知、发 QQ、发表情和身体动作。

## 能力分类

| 能力 | 当前实现/对应模块 | 说明 |
|---|---|---|
| `say` | TTS、QQ 发送、普通回复生成 | 说话只是行动之一 |
| `wait` / `stay_silent` | 调度器记录 self_action | 沉默和旁听也要回写经历 |
| `observe_screen` | `look_at_screen` | 慢感知，结果回写事件 |
| `read_page` | Edge cache | 读取网页/浏览器上下文 |
| `search_web` | `web_search_client` | 自主搜索，结果进入事件流 |
| `search_memory` | memory backend | 回忆相关经历 |
| `write_memory` | `memory_events` / mem0 | 沉淀一段经历 |
| `update_cognition` | `cognition.py` | 更新长期稳定认知 |
| `send_sticker` | QQ sticker library | 社交表达能力 |
| `set_expression` / `move_body` | VTS / portrait | 身体和表情行动 |
| `start_task` / `check_task` | task manager / code exec | 长任务能力 |

## 执行策略

```text
快行动：同步执行，结果立即回写
慢行动：后台执行，开始/完成/失败都回写事件
静默行动：不对外输出，但仍记录 self_action
公开行动：对外说话、发消息或身体表现，必须受边界约束
```

行动选择层输出的是 `ActionBatch`，一个批次可以包含多个行动。执行层按 `mode` 并行启动，并在事件 metadata 中保留：

| 字段 | 用途 |
|---|---|
| `cycle_id` | 标识这次内在叙事流更新后的行动周期 |
| `action_id` | 标识批次中的单个行动 |
| `causality_id` | 标识一条可延续的因果链 |

结果回写格式：

```json
{
  "type": "action_result",
  "source": "search_web",
  "content": "搜索结果摘要……",
  "metadata": {
    "cycle_id": "cycle_20260629_001",
    "action_id": "act_2",
    "causality_id": "cause_abc",
    "action": "search_web",
    "status": "success|failed|timeout|cancelled",
    "elapsed_seconds": 3.4,
    "stale": false
  }
}
```

并行结果必须进入短合并窗口，再由内在叙事流统一吸收；不要让每个工具结果单独触发一次重型更新。紧急事件可以例外。

示例：看屏幕不是对话工具，而是感知行动。

```text
say("我看一下")
observe_screen(async)
observe_screen result → InputEventBus
InnerStreamLoop 吸收结果
行动层再决定是否继续 say
```

## 原则

- 工具结果不是绝对事实，只是带来源、状态和时间的输入事件。
- 工具返回前不能伪造结果。
- 每个行动都应记录原因、状态和结果。
- 不要把“没有输出”当成没有行动；等待和沉默也应可追溯。
- 后续重构目标是让所有工具都走统一 `Action` 接口，而不是分散在对话、QQ、记忆和工具调用路径里。
