# 对话调度器

`DialogueOrchestrator` 是一对一自然对话的新核心。它替代旧逻辑里“对方每说一句，角色就必须立刻完整回复”的假设。

## 目标

角色应该像一个对话中的人，而不是请求处理器。事件到达后，系统先让 LLM planner 判断角色下一步该怎么做：

- 保持沉默
- 轻轻应一声
- 正常开口
- 安排稍后再说
- 只观察并记录，不说话
- 取消或调整待执行计划

程序代码只负责执行决策，不再把人格和话轮规则写成分散的 if/else。

planner 和发言生成器都应从第三人称旁观视角理解这段对话。默认配置里，场上是`真冬`和`爱丽丝`，不是“用户”和“助手”。这样可以减少主仆感：模型判断的是“爱丽丝这个角色自然会怎么反应”，而不是“助手应该怎样满足用户请求”。

## 分层

### 感知层

收集当前场景材料：

- 事件类型和事件文本
- 最近对话历史
- 对话摘要
- 角色资料和完整角色 system prompt 摘要
- cognition 运行时上下文
- emotion 状态
- 可选的屏幕、网页、记忆、直播或工具上下文
- 待执行对话计划

屏幕和网页识别仍然在后台持续更新缓存，因为从零开始读取会很慢。调度器不负责截图或抓网页，只负责判断这些缓存是否值得被讨论。

### Planner

LLM planner 决定下一步对话动作。它会看到角色上下文，并从中推断角色的话轮倾向：

- 文静、克制、内向的角色可以更常选择沉默或短回应
- 活泼、外向、爱接话的角色可以更常开口或安排后续接话
- 认真型角色应减少无意义闲聊
- 爱调侃的角色可以更容易插话或评论

这些倾向不写死在程序里。代码只提供角色资料和上下文，让 planner 自己把人格应用到话轮判断中。

### 发言生成层

只有 planner 选择 `speak` 或 `backchannel` 时才运行。发言生成器会收到较窄的角色提示词和调度结果，例如：

```text
【对话调度决定】
从旁观视角判断，现在轮到爱丽丝可以开口。
动作：speak
模式：normal
意图：回答当前问题
话题：对话架构
```

生成器只负责写出角色此刻会说出口的话，不重新判断是否该说，也不解释调度器。

## 决策格式

planner 只输出 JSON 对象：

```json
{
  "action": "speak",
  "delay_seconds": 0,
  "intent": "回答对方当前问题",
  "topic": "对话架构",
  "utterance_mode": "normal",
  "context_use": "none",
  "memory_policy": "normal",
  "notes": "对方明确要求分析方案"
}
```

支持的 `action`：

- `silence`：听见了，但现在不说话
- `backchannel`：给一句很短的自然回应
- `speak`：正常生成回复
- `schedule`：创建延迟发言计划
- `observe`：只更新上下文，不开口
- `cancel_plan`：取消待执行的延迟计划

`context_use` 控制是否把缓存材料注入生成器：

- `none`：不使用屏幕/网页缓存
- `screen`：使用屏幕缓存
- `page`：使用网页缓存
- `both`：两者都使用

## 事件流

当前实现：

```text
user_utterance -> DialogueOrchestrator.decide()
  silence/observe   -> 记录“听见但未完整回应”，回到空闲
  schedule          -> 记录“听见但未完整回应”，稍后执行
  backchannel/speak -> 带调度上下文调用发言生成
```

屏幕和网页缓存流：

```text
screen_watch / edge_page_cache -> 只更新缓存
DialogueOrchestrator 读取缓存摘要
  context_use=none   -> 忽略缓存
  context_use=screen -> 注入屏幕缓存
  context_use=page   -> 注入 Edge 网页缓存
  context_use=both   -> 同时注入两者
```

旧的 impulse 屏幕/网页计划默认关闭：

```toml
[impulse]
use_screen_context = false
use_edge_page_context = false
```

这样可以保留后台感知的速度，同时把“是否主动讨论屏幕/网页”集中交给同一个 planner。

## 提示词维护

对话调度相关提示词已集中到 `prompts.json` 的 `dialogue_orchestrator` 分组：

- `planner_system`
- `planner_user`
- `generator_context`
- `generator_backchannel_instruction`
- `generator_speak_instruction`
- `system_design_boundary`
- `reply_character_prompt`
- `screen_cache_context`
- `page_cache_context`
- `screen_cache_candidate`
- `page_cache_candidate`
- `observation_marker`
- `scheduled_user_prompt`

后续调整对话风格、第三人称视角、沉默策略、屏幕/网页讨论策略时，优先改这些模板，而不是改程序逻辑。

## 目标架构

最终希望所有会影响对话的话题都变成事件，然后进入同一个调度器：

```text
all events -> DialogueOrchestrator -> executor
```

未来事件可以包括：

- `ai_finished`
- `idle_tick`
- `screen_changed`
- `memory_event`
- `danmaku_event`
- `tts_interrupted`

到那一步后，旧的 `ImpulsePlanner` 可以被折叠成事件生产者和计划执行器，不再作为另一套“对话大脑”存在。

## 迁移说明

第一版仍保留 `ImpulsePlanner`，降低改动风险。当前方向是先稳定用户输入后的话轮决策，再逐步把 impulse 的主动搭话规划迁入同一张对话计划表。

## 测试记录

早期 50 轮 `text_cli.py` 批量测试证明了整体方向可行。后续改成 30 轮批量测试以加快反馈：

- “嗯”这类短回应可以自然变成沉默
- 明确问题仍会触发正常回复
- 关于沉默、人格、话轮的元讨论能较自然地接住
- planner 成本明显，因此 planner 上下文必须保持紧凑

已发现并处理的问题：

- 无状态人格测试时应禁用或隔离长期 cognition/emotion，否则旧话题会泄漏到回复里。
- planner 输出应尽量强制为 JSON。
- 宽泛 session prompt 容易让角色背景变成意外话题材料，因此发言生成现在使用更窄的生成契约。
- 系统设计话题需要单独边界：讨论 planner、impulse、schedule 时直接使用工程概念，不转写成角色世界里的比喻。
- 连续沉默需要由 planner 自己平衡：一两轮沉默可以自然，但长时间沉默通常应给出一点角色仍在场的信号，除非对方明确要求安静。
