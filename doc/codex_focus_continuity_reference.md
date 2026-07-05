# Codex 专注与连续思考机制参考

本文记录对 `D:\work\codex` 的阅读结论，重点关注 Codex 如何维持专注、连续处理工具反馈、管理上下文，以及这些机制对 Alice 的可借鉴之处。

## 结论

Codex 的连续性不是来自后台不断启动新的思考循环，而是来自一个稳定的活动现场：同一时间只有一个 active turn，模型、工具、外部输入、上下文更新都围绕这个 turn 流动。工具调用不会结束思考，工具结果会回填到同一个 turn，随后继续采样。外部输入如果发生在模型运行中，会进入 pending input，在下一次采样时被吸收，而不是另起一个并发思考任务。

对 Alice 来说，最有价值的不是照搬 Codex 的任务助手模型，而是借鉴它的运行骨架：一个主意识现场、增量上下文、工具回流、输入排队、显式压缩、时间提醒。Alice 仍然应该以自主生命体和内在叙事流为核心，程序只负责给 LLM 提供稳定、清晰、有界的现场。

## Codex 的关键机制

### 单一活动现场

Codex 使用 active turn 限制同一时间的模型活动。自动 idle work 只有在没有 active turn、没有 pending trigger turn、不是 Plan mode 时才能启动。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\inject.rs`
- `D:\work\codex\codex-rs\core\src\codex_thread.rs`
- `D:\work\codex\codex-rs\core\src\state\turn.rs`

这让 Codex 避免了多个模型循环同时争夺上下文。它的专注不是靠更多并发，而是靠所有事情进入同一个注意力通道。

Alice 可以借鉴为“主意识现场”：记忆、搜索、屏幕观察、对话输入、工具反馈都可以进入当前现场或下一现场，但不应该各自直接启动独立主循环。

### Turn 内连续采样

Codex 的 `run_turn` 是一个循环。模型输出工具调用后，运行工具，把工具结果写回历史，再继续采样。只要 `model_needs_follow_up` 或 `has_pending_input` 为真，turn 就继续。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\turn.rs`

这点对 Alice 很重要。现在 Alice 的 life tick 如果把工具调用、工具结果理解、下一步意识活动割裂成多个冷启动 tick，就容易出现思维断裂和效率低。更合理的结构是：一次生命活动可以包含多次快速模型调用，工具结果回来后立刻在同一现场继续理解。

### Pending Input

Codex 把运行中到来的输入放进 input queue。模型运行时，外部输入不直接打断，而是在合适的边界进入下一次采样。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\input_queue.rs`
- `D:\work\codex\codex-rs\core\src\session\mod.rs`

Alice 可以借鉴为信息池：调试输入、对话输入、工具反馈、记忆事件都进入一个有顺序的输入池。程序负责排队和边界，LLM 负责理解含义和决定下一步。

### 增量上下文

Codex 明确要求 model visible context 增量构建，不频繁重写，所有注入项都有硬上限。历史通过 `ContextManager` 记录，发送给模型前再规范化和截断。

相关位置：

- `D:\work\codex\AGENTS.md`
- `D:\work\codex\codex-rs\core\src\context_manager\history.rs`

Alice 应该保留 `inner_stream.txt` 作为自主决策痕迹，但运行时 prompt 不应该反复塞入巨大全量状态。更适合的方式是稳定底座加小块 delta：最近事件、当前时间、当前现场、短期摘要、记忆召回、工具反馈都以有界片段进入。

### 类型化上下文碎片

Codex 所有注入上下文都通过 `ContextualUserFragment`，内部上下文还带 source 标记，便于审计、调试、压缩和回放。

相关位置：

- `D:\work\codex\codex-rs\core\src\context\mod.rs`
- `D:\work\codex\codex-rs\core\src\context\internal_model_context.rs`

Alice 可以借鉴这个骨架。时间、记忆召回、短期压缩、工具反馈、屏幕观察、调试输入都应该作为可追踪上下文碎片进入 prompt。程序只标记来源、时间、大小和顺序，不做语义分类决策。

### 时间提醒

Codex 的 `CurrentTimeReminder` 会按窗口、间隔、用户或工具输出边界注入当前时间。它不是每个请求都塞一大段时间信息，而是在合适边界提供简短时间锚点。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\time_reminder.rs`
- `D:\work\codex\codex-rs\core\src\context\current_time_reminder.rs`

Alice 的生命活动需要更强时间观念。时间让思考有意义，也让她能想起自己刚才在做什么、多久没行动、还有什么牵挂。时间应该是高频可见但简短的上下文碎片。

### 显式压缩

Codex 的上下文压缩是显式阶段，而不是在各处偷偷截断。它有 `compact_conversation_history` 和 `new_context`，压缩会成为可追踪的上下文窗口切换。

相关位置：

- `D:\work\codex\codex-rs\core\src\client.rs`
- `D:\work\codex\codex-rs\core\src\tools\handlers\new_context_window_spec.rs`

Alice 的短期记忆和内在叙事流压缩也应该显式化。压缩前后需要有日志、摘要、保留点和 token/字符量变化。压缩不是丢弃，而是把现场整理成更高密度的上下文。

### 可见注意力锚点

Codex 的 `update_plan` 是一个简单的状态工具，只维护当前任务步骤和状态。它帮助模型保持任务连续性。

相关位置：

- `D:\work\codex\codex-rs\core\src\tools\handlers\plan_spec.rs`

Alice 不应该照搬任务助手式 plan，但可以拥有 LLM 自己维护的注意力锚点，例如“当前牵挂”“正在追的方向”“下一步想做的事”。这应该是角色自己写出来的内在状态，不是程序分类出来的任务队列。

## 对 Alice 的架构启发

Alice 的核心仍然是内在叙事流。程序不应该替代 LLM 决策，也不应该用大量硬编码分类模拟思考。程序应该提供的是稳定生命现场：

- 一个主意识现场，避免多个模块各自推着系统跑。
- 一个输入池，接收外部输入、工具反馈、记忆事件和调试输入。
- 一套有来源、有时间、有大小上限的上下文碎片。
- 一套工具回流机制，让工具结果能在同一生命现场继续被理解。
- 一套显式短期压缩机制，保证上下文信息密度。
- 一个持续可见的时间锚点。
- 一个由 LLM 自己维护的当前牵挂或意图状态。

最终目标不是让程序设计她怎么想，而是让 LLM 在足够稳定、快速、清晰的现场中持续自主思考。

## 不应照搬的部分

Codex 是 coding agent，核心目标是完成用户任务。Alice 是 AI 生命体，核心目标是持续自主生活和思考。因此以下部分不适合直接照搬：

- 用户任务优先的 turn 语义。
- 强任务化的 plan 表达。
- 以完成最终回答为结束条件的思考结构。
- 过多由程序决定的工作模式切换。

Alice 可以借鉴 Codex 的骨架，但提示词仍然是核心。程序负责现场、边界、流速和记录；LLM 负责理解、兴趣、选择、行动和连续自我。
