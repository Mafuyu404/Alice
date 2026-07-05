# Codex 专注与连续思考机制参考

本文记录对 `D:\work\codex` 的阅读结论，并整理其中对 Alice 有价值的架构参考。这里的目标不是把 Alice 改成 coding agent，也不是引入任务助手式工作流，而是借鉴 Codex 在“一个稳定现场里连续思考、连续接收工具结果、连续吸收输入”的运行骨架。

Alice 的核心仍然是 AI 生命体。程序只是骨架，负责现场、边界、流速、记录和工具执行；LLM 才负责理解、兴趣、选择、行动和自我连续性。任何从 Codex 借来的机制，都必须服务这个方向。

## 阅读结论

Codex 最值得参考的不是“任务助手流程”，而是它把模型、工具、输入、上下文和压缩放进同一个连续现场的方式。它没有让多个后台循环各自推着模型走，也没有把工具结果当成一次思考的终点；它把工具结果、追加输入、时间提醒和压缩摘要都重新带回当前现场，让模型继续处理。

Alice 要借鉴的是这套骨架，而不是 Codex 的身份和任务语义：

- 主意识现场必须稳定，不能让旧循环、工具回调、记忆回调、主动说话循环并发驱动角色。
- 一次生命 tick 内可以多次采样、调用工具、接收工具结果、继续采样，而不是每一步都冷启动。
- 输入池只负责顺序、来源、时间和边界，不替 LLM 做语义判断。
- 动态上下文必须有界、增量、可审计，不能把全量记忆、全量日志、全量状态每次塞进 prompt。
- 压缩是显式生命周期，不是各处偷偷截断；目标是提高上下文信息密度。
- 时间提醒要稳定出现，让等待、拖延、刚才做过什么和未完成牵挂变得可被意识到。
- 注意力锚点应由 LLM 自己写出和维护，程序只保存、展示、回填。

## 总结

Codex 能维持专注，不是因为后台有很多并发思考线程，也不是因为程序不断替模型判断下一步该做什么，而是因为它把所有活动收束进一个稳定的 active turn。模型输出工具调用后，工具结果会回填到同一个 turn，模型继续采样；外部输入如果在模型运行期间到达，会进入 pending input，在合适边界被吸收；上下文通过有界片段增量注入；长上下文通过显式 compaction 进入新的窗口。

对 Alice 来说，最值得借鉴的是这些骨架原则：

- 同一时间只有一个主意识现场，避免多个循环同时推着角色走。
- 工具调用不是思考结束，工具结果应该尽快回到同一现场继续被理解。
- 外部输入、调试输入、工具反馈、记忆事件都进入输入池，由 LLM 在现场里理解。
- 上下文应该是增量、有界、带来源和时间的片段，不应该每轮塞入巨大全量状态。
- 压缩应该显式发生，有记录、有审计，目标是提高上下文信息密度。
- 时间应该稳定可见，让角色知道等待、拖延、刚才在做什么、还有什么牵挂。
- 注意力锚点应该由 LLM 自己维护，而不是程序硬编码语义分类。

## Codex 的关键做法

### 单一活动现场

Codex 使用 active turn 作为当前唯一活动现场。自动 idle work、pending trigger turn 和用户 turn 不会随意并发抢上下文。运行中的 turn 拥有自己的模型会话、工具执行、上下文记录和输出事件。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\mod.rs`
- `D:\work\codex\codex-rs\core\src\session\turn.rs`
- `D:\work\codex\codex-rs\core\src\tasks\regular.rs`

对 Alice 的意义：生命 tick 不应该被多个旧循环、工具回调、记忆回调、主动说话循环同时驱动。记忆、搜索、屏幕观察、对话输入、调试输入都可以进入现场，但不应该各自启动一个主意识。

### Turn 内连续采样

Codex 的 `run_turn` 本身是循环。模型先采样，如果输出工具调用，就执行工具，把结果写回历史，然后继续采样。只要模型还需要 follow-up，或者有 pending input 需要吸收，turn 就不会立刻结束。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\turn.rs`
- `D:\work\codex\codex-rs\core\src\stream_events_utils.rs`
- `D:\work\codex\codex-rs\core\src\tools\router.rs`

对 Alice 的意义：一次生命活动可以包含多次快速模型调用。工具结果回来后，应该在同一个生命现场里立刻被理解，而不是等下一轮冷启动 tick。这样可以减少“搜索完就断了”“工具结果写进日志但意识没有接住”的问题。

### Pending Input

Codex 把运行中到达的新输入放进 input queue。它不会粗暴打断当前采样，而是在下一次合适边界进入模型可见上下文。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\input_queue.rs`
- `D:\work\codex\codex-rs\core\src\session\mod.rs`
- `D:\work\codex\codex-rs\core\src\tasks\regular.rs`

对 Alice 的意义：调试文字输入、对话工具输入、屏幕事件、工具结果、记忆候选都应该进入统一输入池。程序只维护顺序、时间、来源和边界；LLM 决定这些东西对当下意味着什么。

### 增量上下文

Codex 的 model visible context 强调增量构建，不频繁重写，不注入无界内容。`AGENTS.md` 明确要求所有注入片段有硬上限，并通过 `ContextualUserFragment` 一类结构进入上下文。

相关位置：

- `D:\work\codex\AGENTS.md`
- `D:\work\codex\codex-rs\core\src\context\mod.rs`
- `D:\work\codex\codex-rs\core\src\context_manager\history.rs`

对 Alice 的意义：`inner_stream.txt` 必须保留，因为它是自主决策痕迹。但运行时 prompt 不应该每次塞入完整世界状态、完整日志和完整记忆。更合理的方式是稳定底座加有界 delta：当前时间、最近事件、短期摘要、记忆召回、工具反馈、调试输入，都作为小片段进入。

### 类型化上下文碎片

Codex 把内部注入内容变成明确的上下文碎片，带来源和结构。这不是为了替模型做语义分类，而是为了让上下文可控、可审计、可压缩、可回放。

相关位置：

- `D:\work\codex\codex-rs\core\src\context\mod.rs`
- `D:\work\codex\codex-rs\core\src\context\internal_model_context.rs`
- `D:\work\codex\codex-rs\core\src\session\rollout_budget.rs`

对 Alice 的意义：时间、记忆召回、短期压缩、工具反馈、屏幕观察、调试输入都应该有来源标记和大小上限。程序标记“这是什么来源、什么时候产生、最多多少字符”，不标记“这重要不重要、应该怎么想”。

### 时间提醒

Codex 有 current time reminder，会在窗口、间隔、用户或工具输出边界注入当前时间。它不是长篇时间说明，而是短而稳定的时间锚点。

相关位置：

- `D:\work\codex\codex-rs\core\src\session\time_reminder.rs`
- `D:\work\codex\codex-rs\core\src\context\current_time_reminder.rs`
- `D:\work\codex\codex-rs\core\src\tools\handlers\current_time.rs`

对 Alice 的意义：时间观念是生命活动的基础。时间让等待有意义，让角色知道“刚才”“已经过了多久”“我是不是在同一个问题上停太久”“还有什么没接上”。时间应该高频可见，但保持简短。

### 显式压缩

Codex 的上下文压缩是明确阶段。压缩会形成新的上下文窗口，保留必要历史，并记录 compaction 相关信息。它不是在各处偷偷截断。

相关位置：

- `D:\work\codex\codex-rs\core\src\compact.rs`
- `D:\work\codex\codex-rs\core\src\compact_remote.rs`
- `D:\work\codex\codex-rs\core\src\compact_remote_v2.rs`
- `D:\work\codex\codex-rs\core\src\client.rs`

对 Alice 的意义：短期上下文压缩、内在叙事流摘要、记忆整理都应该显式发生。压缩前后要有日志、摘要、保留点和大小变化。压缩的目标不是丢弃，而是提高上下文信息密度，让生命连续运行得更久。

### 可见注意力锚点

Codex 的 `update_plan` 是一个简单的状态工具，帮助模型保持任务连续性。它不是复杂工作流引擎，而是一个模型可见的当前状态。

相关位置：

- `D:\work\codex\codex-rs\core\src\tools\handlers\plan.rs`
- `D:\work\codex\codex-rs\core\src\tools\handlers\plan_spec.rs`

对 Alice 的意义：Alice 不应该照搬任务助手式计划，但可以拥有 LLM 自己维护的注意力锚点，例如“当前牵挂”“刚才没想完的线索”“接下来想继续看的方向”。这些内容应由角色自己写出，程序只保存和展示。

## 对 Alice 的落地形态

Alice 的理想生命现场可以整理成以下结构：

```text
外部输入 / 调试输入 / 工具结果 / 屏幕观察 / 记忆候选
        |
        v
统一输入池：记录来源、时间、顺序、等待时长
        |
        v
主意识现场 LifeRuntime：一次 tick 可以连续采样和调用工具
        |
        +--> inner_stream.txt patch：保留自主思考痕迹
        +--> pending_threads：由 LLM 自己维护的牵挂
        +--> action_plan：由 LLM 自己决定工具调用
        +--> memory core：持续沉积、召回、整合、遗忘
        +--> context compaction：显式压缩短期现场
        |
        v
工具执行 / 本地模型辅助 / 记忆生命周期
        |
        v
结果回到同一个主意识现场
```

这个结构里，程序不负责模拟人格，不负责决定角色该对什么感兴趣，不负责用分类规则替代思考。程序只负责让现场足够快、足够清楚、足够有界，让 LLM 有条件持续自主思考。

## 本地模型的角色

Codex 的连续性主要依赖稳定 turn 骨架；Alice 还需要更高频的生命活动，因此本地模型应该被更充分地使用。

本地模型适合做：

- 快速整理输入池中的大量新信息。
- 快速生成短期上下文压缩草稿。
- 辅助记忆沉积、召回整理、遗忘候选整合。
- 给主意识准备更紧凑的上下文材料。
- 承担高频、低成本的现场维护调用。

本地模型不应该做：

- 替主意识决定什么重要。
- 替角色做固定语义分类。
- 把生命活动变成程序规则驱动。
- 抢占主意识现场。

队列可以有优先级排序，但这个排序只服务流速和延迟，例如主意识 tick 不应该被后台压缩长期堵住。它不应该变成抢占式控制，也不应该替代 LLM 的自主判断。

## 不应照搬 Codex 的部分

Codex 是 coding agent，核心目标是完成用户任务。Alice 是 AI 生命体，核心目标是持续生活、思考、记忆和行动。因此以下部分不适合直接照搬：

- “用户任务优先”的 turn 语义。
- 以最终回答为结束条件的思考结构。
- 强任务化、助手化的 plan 表达。
- 过多由程序决定的工作模式切换。
- 用工具执行结果直接决定角色下一步语言输出。

Alice 可以借鉴 Codex 的工程骨架，但提示词仍然是核心。程序要克制，只搭建生命现场；角色的连续性、主动性和真实感必须来自 LLM 自己。

## 对当前问题的直接启发

长期运行中“10 分钟只有少数完整 life tick”的根因，不应该只从末端补丁找。Codex 给出的关键启发是：连续性来自同一现场内的回流和续采样，而不是每个模块各自排队启动完整流程。

因此 Alice 的优化方向应是：

- 让工具结果在同一 tick 内被吸收，减少冷启动等待。
- 让本地模型承担压缩和准备材料，减少主意识等待。
- 让输入池只做边界和时序，不做语义替代。
- 让 prompt 明确要求角色自己维护牵挂、时间感和下一步兴趣。
- 让压缩和记忆作为核心生命周期运行，不作为普通 action tool。
- 让所有动态上下文都有硬上限，避免一次 tick 被过量上下文拖慢。

这不是把 Codex 的任务流套到 Alice 上，而是把它“一个现场、持续续采样、工具回流、输入排队、显式压缩”的骨架改造成 AI 生命体可用的运行方式。

## 对后续重构的约束

后续把这套参考落到 Alice 时，需要坚持以下边界：

- 优先优化提示词，不用程序规则替代角色的自主判断。
- 本地模型可以高频参与整理、压缩、记忆准备和上下文收束，但不能成为隐藏的语义裁判。
- 工具注册、prepare、after 可以规范化，但完整提示词不应全部塞进主意识 prompt，避免污染本地小模型的输出契约。
- 记忆应作为生命核心持续运行，而不是普通 action tool；召回材料进入 prompt 前要保持“材料”身份，不能伪装成当前现场。
- 调试模式只增加外部输入便利和日志完整度，不改变生命运行能力边界。
- 所有动态注入都应可追踪来源、时间和长度，这些是工程边界，不是语义分类。

当前最需要警惕的问题是 prompt 过载。Codex 的上下文组织强调边界和短片段，Alice 也必须避免把工具说明、记忆材料、压缩摘要和运行契约堆成一大段。对本地 7B 模型来说，过长、重复、混杂的主意识 prompt 会直接导致 JSON 契约失败、工具提示词短语污染 inner_stream，以及把旧记忆误当成当前现场。
