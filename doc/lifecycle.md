# 生命周期架构

Alice 的核心不应是“对话”，而是一个持续运行的生命循环：

```text
信息事件 → 内在叙事流 → 行动能力 → 行动结果 → 信息事件
```

人类说话、QQ 消息、屏幕变化、网页内容、时间流逝、工具结果和自身行动结果都只是信息事件。说话、沉默、观察、搜索、写记忆、更新认知、发 QQ、发表情、启动任务也都只是可执行行动。系统持续把事件吸收进内在叙事流，再从内在叙事流里选择下一步行动。

## 三个核心

### 1. 内在叙事流

`inner_stream` 是唯一的连续主体状态。它维护“我现在作为我还在想什么”，包括注意力、旁路输入、悬挂线索、行动倾向和边界。

它不是任务列表，也不是程序规则。Python 不应解析其中栏目做 if/else；它只作为 LLM 可读的连续心理文本。

### 2. 行动能力

所有可采取行为都应能力化：

| 行动 | 含义 |
|---|---|
| `say` | 对本地用户、QQ、群聊或其他通道说话 |
| `stay_silent` / `wait` | 明确选择沉默、旁听、等待 |
| `observe_screen` | 看屏幕或请求视觉识别 |
| `read_page` | 读取当前网页/缓存 |
| `search_web` | 搜索公开信息 |
| `search_memory` | 回忆已有记忆 |
| `write_memory` | 沉淀一段经历 |
| `update_cognition` | 更新稳定认知 |
| `send_sticker` | 发送表情包 |
| `set_expression` / `move_body` | 表情和身体动作 |
| `start_task` / `check_task` | 启动或查看长期任务 |

“工具调用”只是行动能力的一种实现方式。对系统来说，`say` 和 `search_web` 一样都是行动；`wait` 和 `stay_silent` 也不是空操作，而是可记录的选择。

### 3. 信息循环

任何行动都必须回写为事件：

```json
{
  "type": "self_action",
  "source": "life_loop",
  "content": "我选择先看一下屏幕，而不是直接猜测。",
  "metadata": {
    "action": "observe_screen",
    "status": "started"
  }
}
```

行动完成、失败、超时、取消也都回写：

```json
{
  "type": "action_result",
  "source": "observe_screen",
  "content": "屏幕识别结果：前台窗口是浏览器，页面里有一个 Python 报错。",
  "metadata": {
    "action": "observe_screen",
    "status": "success",
    "elapsed_seconds": 7.2
  }
}
```

这样系统不会把工具结果当成上一轮对话的附属材料，而是把它作为新的现实输入吸收。

## 主循环

```text
InputEventBus
  收集外部输入、自身行动、行动结果、时间心跳
      ↓
InnerStreamLoop
  合并事件，更新内在叙事流
      ↓
LifeLoop / ActionPolicy
  读取内在叙事流、近期事件、可用能力和安全边界
  选择一个 ActionBatch（可包含多个并行动作）
      ↓
ActionRuntime
  执行 say / search / observe / memory / body / wait ...
      ↓
ExperienceFeedback
  将行动开始、完成、失败、沉默和等待回写为事件
```

事件可以很快进入，也可以延迟吸收。慢行动不阻塞生命循环：例如看屏幕需要七秒，角色可以先 `say("我看一下")`，同时启动 `observe_screen`；识别结果回来后再作为新事件进入循环。

## 行动批次

内在叙事流更新后，行动选择层不应只输出单个行动。一次心理状态可能自然对应多个并行动作，例如先说“我看一下”、同时启动屏幕识别、再静默沉淀一条记忆。建议输出 `ActionBatch`：

```json
{
  "cycle_id": "cycle_20260629_001",
  "causality_id": "cause_abc",
  "reason": "当前内在叙事流里同时有回应、观察屏幕和记录经历的倾向",
  "actions": [
    {
      "action_id": "act_1",
      "action": "say",
      "reason": "先接住用户的请求",
      "args": {"channel": "speech", "text": "我看一下。"},
      "mode": "sync",
      "visibility": "public",
      "result_policy": "feed_back"
    },
    {
      "action_id": "act_2",
      "action": "observe_screen",
      "reason": "需要真实视觉输入，不能凭空回答",
      "args": {"focus": "用户刚才问的屏幕报错"},
      "mode": "async",
      "visibility": "private",
      "result_policy": "trigger_next_step"
    }
  ]
}
```

字段含义：

| 字段 | 含义 |
|---|---|
| `cycle_id` | 一次内在叙事流更新后的行动选择周期 |
| `action_id` | 批次内单个行动的唯一标识 |
| `causality_id` | 因果链标识，用来串起后续结果、重试和派生行动 |
| `mode` | `sync` 立即执行，`async` 后台执行 |
| `visibility` | `public` 对外可见，`private` 内部感知，`silent` 静默维护 |
| `result_policy` | `feed_back` 回写事件，`record_only` 只记录，`trigger_next_step` 结果回来后可触发新一轮行动选择 |

## 并行执行与结果合并

同一个 `ActionBatch` 中的行动可以并行执行，但结果不能每回来一个就立刻重写一次内在叙事流。需要一个短结果合并窗口：

```text
ActionRuntime 并行启动 actions
  → action_started 事件进入队列
  → action_result / action_failed 进入 pending_events
  → 按 cycle_id / causality_id 合并 0.5-2 秒
  → InnerStreamLoop 一次性吸收合并结果
```

规则：

- `action_started` 默认只记录，不触发重型内在叙事流；公开行动如 `say` 可以立即回写。
- 普通 `action_result` 进入短窗口合并，避免多个工具同时完成时连续刷 LLM。
- 慢行动超过合并窗口后单独返回；若已过时，标记 `stale=true`，默认降级为观察。
- `urgent` 结果可以绕过合并窗口立即触发。
- `record_only` 结果只进入经历记录或低频吸收，不主动触发下一轮行动。
- ActionSelector 不直接递归调用自己；必须等待结果被内在叙事流吸收后，再进入下一轮选择。
- 每个批次应有限制，例如最多 3 个行动，公开输出最多 1 个。

结果事件必须带回三个 ID：

```json
{
  "type": "action_result",
  "source": "observe_screen",
  "content": "屏幕识别结果：前台窗口是浏览器，页面里有一个 Python 报错。",
  "metadata": {
    "cycle_id": "cycle_20260629_001",
    "action_id": "act_2",
    "causality_id": "cause_abc",
    "action": "observe_screen",
    "status": "success",
    "elapsed_seconds": 7.2,
    "stale": false
  },
  "priority": "normal"
}
```

## 与现有模块的对应

| 新概念 | 当前模块 |
|---|---|
| 信息事件 | `kokoro/core/input_events.py` |
| 内在叙事流 | `kokoro/core/inner_stream.py` |
| 行动批次 | `kokoro/action/model.py` |
| 行动运行时 | `kokoro/action/runtime.py` |
| 行动选择雏形 | `kokoro/action/autonomous_step.py`, `kokoro/action/dialogue_orchestrator.py`, `kokoro/action/agent_guard.py` |
| 行动执行 | `kokoro/action/tool_registry.py`, `kokoro/action/tool_handlers.py`, TTS/QQ/VTS/记忆模块 |
| 经验回写 | `kokoro/core/chat_session.py`, `kokoro/core/memory_events.py` |

后续重构方向是把 `dialogue_orchestrator`、`agent_guard`、`autonomous_step` 收束到同一套行动选择接口里：对话不再是中心，而是 `say` 行动的一种通道。

## 保留的快速路径

语音实时对话可以保留快速路径，避免每句话都等待完整内在叙事流更新：

```text
用户语音事件
  ├─ 快速行动选择：是否立刻 say / backchannel / wait
  └─ InnerStreamLoop：异步吸收为经历
```

这不是回到对话中心，而是为了实时性保留一个低延迟行动通道。所有结果仍要回写事件，并最终进入内在叙事流。

## 设计边界

- 人类不是默认中心；人的话只是高优先级输入源之一。
- 说话不是默认输出；沉默、观察、搜索、记忆、等待都是正常行动。
- 工具结果不是事实本身，而是带来源和状态的输入事件。
- 慢行动不阻塞当前生命循环。
- 每个行动都要可追溯：为什么做、做了什么、结果如何。
- 长期自主性来自事件和行动沉淀，不来自随机触发或固定日程。
