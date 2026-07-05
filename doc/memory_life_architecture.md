# AI 生命体记忆系统架构设计

本文定义 Alice 新一版记忆系统。它参考 AIRI 的长期记忆方向和 Open-LLM-VTuber 的会话历史工程细节，但目标不是复刻任何一个项目，而是为 Alice 当前的 AI 生命体架构服务。

核心原则不变：程序只是骨架，记忆系统提供经历材料、召回能力、整理能力和可追溯日志；真正的理解、取舍、情绪关联、行动选择和内在叙事更新，仍然交给 LLM 自主完成。

## 设计目标

记忆系统要支撑的是一个持续存在的 AI 生命体，而不是一个问答助手。它应该让角色能做到：

- 记得自己经历过什么。
- 在合适的时候自然想起相关经历。
- 能把过去经历和当前处境联系起来。
- 能从经历中形成稳定认知，但不被一次性噪声污染。
- 能恢复最近的生命连续性，而不是每次启动都像重置。
- 能保留大量记忆，同时召回足够准。
- 能让本地模型高频参与整理、压缩、召回准备和记忆写入准备。

因此新系统的中心不是 mem0，也不是数据库 schema，而是这条闭环：

```text
生命活动 / 对话 / 工具结果 / 时间流逝
  -> 原始经历日志
  -> 本地模型快速吸收与事件切分
  -> 情节记忆与短期时间线
  -> 后台整理、合并、压缩、索引
  -> 长期记忆与认知材料
  -> 多路召回与重排
  -> inner_stream / 工具 prepare / 表达
  -> 新的生命活动
```

## 外部参考的取舍

### 从 AIRI 借鉴什么

AIRI 的价值在于它指出了角色记忆不能只是向量搜索。它强调记忆需要时间、重要性、情绪影响、访问强化、上下文窗口、遗忘和突然想起。

Alice 应该吸收这些方向：

- 记忆条目需要元数据，而不是只有一句文本。
- 召回需要候选召回和重排，而不是 top-k vector search。
- 命中记忆后要扩展上下文，召回一段经历，而不是一个孤立事实。
- 长期记忆不应直接从原始对话写入，应经过事件切分和整合。
- 需要后台整理线程，持续把生命活动日志转成更高密度的可召回记忆。

但 Alice 不应该把 AIRI 的“分层”变成硬编码心理分类。这里需要的是工程上的材料形态和检索连接，不是让程序替 LLM 判断“这是什么心理内容”。所有分类词都应该尽量退到数据管理层，只说明材料怎么保存、怎么找回、怎么防污染。

### 从 Open-LLM-VTuber 借鉴什么

Open-LLM-VTuber 当前没有真正的长期记忆。它有价值的是工程细节：

- 原始 chat history 稳定保存。
- 每个角色配置有独立 ID，避免历史冲突。
- 启动或切换会话时可以从历史恢复近期上下文。
- 主动说话、控制信号、调试触发可以通过 `skip_memory` / `skip_history` 避免污染。
- 中断被作为一段真实经历写入上下文，而不是简单丢弃。
- 多人对话里用增量索引避免每个参与者反复吞同一批消息。

Alice 应该吸收这些点：原始经历日志必须可回放，记忆写入必须有污染边界，恢复生命连续性不能只靠语义召回。

## 总体架构

新记忆系统分成六个模块：

```text
MemoryEventLog        原始经历日志
MemoryWorkingContext  近期生命连续性
MemoryConsolidator    本地模型后台整理
MemoryStore           Alice 自己的记忆记录与元数据
MemoryIndex           mem0 / embedding / 关键词索引
MemoryRecall          多路召回、重排、上下文扩展
```

它们和当前系统的关系：

```text
LifeRuntime
  -> InformationPool
  -> MemoryEventLog
  -> MemoryWorkingContext
  -> MemoryRecall.default_context()
  -> life tick prompt
  -> LLM 自主决定 inner_stream patch / 工具调用

memory tool
  -> MemoryRecall.deep_recall()
  -> MemoryConsolidator.prepare_write()
  -> MemoryStore.write()
  -> MemoryIndex.sync()
```

`inner_stream.txt` 仍然是主体连续性的中心。记忆系统不能取代 inner_stream，也不能把 inner_stream 变成数据库状态表。记忆系统只负责提供“她可能会想起的材料”。

## 原始经历日志

所有进入生命体的信息，都先进入 append-only 原始日志。这里保存事实，不做最终判断。

建议路径：

```text
characters/{character_id}/memory/events/YYYY-MM-DD.jsonl
```

每条事件建议结构：

```json
{
  "event_id": "evt_...",
  "character_id": "snow",
  "timestamp": "2026-07-05T10:20:30+08:00",
  "source": "dialogue | tool_result | inner_stream | qq | live | screen | debug | system",
  "content": "...",
  "participants": ["真冬", "雪吱"],
  "tool_name": "search_web",
  "memory_policy": "experience | control | debug | ephemeral | blocked",
  "links": {
    "inner_stream_version": 12,
    "action_id": "act_..."
  }
}
```

这里的 `memory_policy` 很关键。它不是人格规则，也不是记忆意义分类，而是防污染边界。它只回答“这段文本能不能进入整理流程”，不回答“它对角色意味着什么”：

- `experience`：允许进入整理流程的生命材料。
- `control`：程序控制信号，不进入角色记忆。
- `debug`：调试输入，默认不进入长期记忆，除非明确被角色吸收为经历。
- `ephemeral`：临时材料，可进入近期上下文，不沉淀。
- `blocked`：隐私、错误、机械日志等，不参与记忆。

这解决 Open-LLM-VTuber 里 `skip_memory` 的问题，但表达成更适合 Alice 的通用事件策略。

## 近期生命连续性

长期记忆不是唯一记忆。角色启动、运行、即时对话最需要的是近期生命连续性。

建议继续使用并扩展当前 context 文件：

```text
characters/{character_id}/context/live_timeline.txt
characters/{character_id}/context/recent_digest.txt
characters/{character_id}/context/pending_threads.txt
characters/{character_id}/context/tool_results_digest.txt
characters/{character_id}/context/recent_memory_digest.txt
```

这些文件不是给程序硬判断的状态表，而是给 LLM 阅读的高密度文本材料。

`recent_memory_digest.txt` 记录最近被想起、刚写入、刚改变意义的记忆。它解决一个常见问题：语义召回命中了某条记忆，但下个 tick 又忘了刚才想起过什么。

近期上下文的整理由本地模型高频执行，目标只有一个：用更少 token 保留更多连续经历。

## Alice 自己的记忆记录

mem0 不作为记忆本体。Alice 需要自己的 memory record。

建议路径：

```text
characters/{character_id}/memory/store.sqlite
```

核心表可以先简单：

```text
memory_records
  id
  character_id
  record_form
  content
  summary
  created_at
  updated_at
  last_accessed_at
  access_count
  importance
  emotional_impact
  keywords_json
  tags_json
  source_event_ids_json
  evidence_json
  related_memory_ids_json
  index_status
  deleted_at
```

这里使用 `record_form`，不要使用 `kind`。它只描述这条材料的保存形态，不描述角色心理，也不允许程序根据它决定重要性或行动。

- `raw_event`：原始事件或其极短摘录。
- `episode_note`：一段具体经历的整理文本。
- `distilled_note`：从多段经历中提炼出的稳定材料。
- `open_thread`：仍悬着、未说完、未完成或之后可能继续处理的线索。
- `association_note`：用于连接多段经历的关联说明。

这些不是让程序判断“她应该怎么想”，而是让检索和整理知道材料长什么样。真正哪些内容重要、是否改变自己、是否该继续想，由 LLM 在 prompt 中判断。

## 写入流程

记忆写入必须从“原始材料”变成“经历”，再变成“长期材料”。不能把一轮对话直接塞进 mem0。

推荐流程：

```text
raw events
  -> local LLM: event extract
  -> candidate experience notes
  -> local LLM: merge / dedup / evidence attach
  -> MemoryStore.write(episode_note)
  -> local LLM or stronger LLM: distillation
  -> MemoryStore.write(distilled_note / open_thread / association_note)
  -> MemoryIndex.sync()
```

本地模型的职责：

- 从事件流里提炼候选经历。
- 合并重复描述。
- 保留具体名词、时间、人物、工具结果证据。
- 判断调试/控制/机械日志是否应该排除。
- 生成关键词和短摘要。
- 为 mem0 准备适合 embedding 的文本。

强模型只在必要时介入：

- 复杂情感关系变化。
- 长期认知变化。
- 多段经历之间的高价值整合。
- 本地模型输出不稳定或冲突时复核。

写入不是“程序决定什么值得记”，而是“程序把材料送给 LLM 做记忆整理”。程序只负责保存、索引、去重和追溯。

## mem0 的位置

mem0 保留，但定位降级：

```text
mem0 = vector index backend
Alice MemoryStore = 角色记忆本体
```

mem0 负责：

- embedding
- 向量存储
- 向量相似候选
- 基础相似候选

mem0 不负责：

- 角色记忆的真实性判断。
- 事件到经历的切分。
- 认知变化。
- 访问强化。
- 上下文扩展。
- 记忆污染控制。
- 启动恢复。

写入 mem0 的内容应该来自 `MemoryStore` 的整理结果，而不是原始对话。

mem0 中的 `user_id` 继续只使用角色维度：

```text
user_id = character_id
```

不再引入对方、场景、域等额外 namespace。所有记忆属于角色自身。

## 召回流程

召回分成默认召回和主动深挖。

默认召回由 LifeRuntime 每次 tick 自动做，给 LLM 一小批可能相关的记忆材料。主动深挖由 `search_memory` 工具完成，让 LLM 自己决定是否继续查、查什么、查几次。

召回应该允许高频发生。人本来就会频繁想起东西，尤其是在对话、研究、等待、受刺激、看到相似场景时。架构不应该把记忆召回当成昂贵稀有动作。真正需要控制的不是召回频率，而是召回材料进入提示词的方式：

```text
高频召回可以发生
  但每次注入要短
  关联弱的内容要压缩
  记忆呈现要像背景材料
  不要逼 LLM 表演“我突然想起”
```

召回应使用多路候选：

```text
query = event_batch + inner_stream + recent_digest + pending_threads

candidates =
  recent timeline matches
  + open thread matches
  + MemoryStore keyword/exact matches
  + MemoryStore recent important records
  + mem0 vector candidates
  + recently accessed memories
  + small spontaneous recall sample
```

然后重排：

```text
score =
  vector relevance
  + keyword / exact match
  + recency
  + importance
  + emotional impact
  + access reinforcement
  + unfinished clue relevance
  + small randomness
```

这不是替 LLM 决定“该想什么”，而是把更可能自然浮现的材料放到它面前。

最终注入给 LLM 的不应该是数据库字段，而是自然、紧凑、带来源感的文本：

```text
可能浮现的记忆：
- 三天前，雪吱研究过 Create 里的机械臂和物品运输，当时她卡在如何稳定分拣矿物；后来她把这件事留成了“想继续查自动化流程”的线索。
- 今天早些时候，她看过一段关于红石时钟的资料，但还没有把它和自己的基地规划联系起来。
```

更推荐的提示词语气是“背景中可用的记忆材料”，而不是“你突然想起”。例如：

```text
可作为当前思考背景的记忆：
- ...

与当前线索弱相关、只需知道其存在：
- ...
```

要避免频繁出现这类表达：

```text
我突然回忆起...
我脑海里闪过...
这让我想起...
```

这些表达可以偶尔自然出现，但不能由系统提示词强行制造。记忆材料被注入 prompt 后，LLM 可以选择完全不明说，只让它影响语气、判断、下一步行动或 inner_stream 的细微变化。

## 召回提炼与关联压缩

高频召回的前提是召回结果足够轻。不能每次把一堆旧经历原样塞进 prompt。

MemoryRecall 应该把候选按关联强度分成三种呈现密度：

```text
强相关：
  保留具体时间、对象、当时发生的事、后续线索。

中相关：
  保留一句高密度摘要，必要时带一个具体锚点。

弱相关：
  只保留“存在这个背景”的短线索，或只进入 hidden/internal recall digest，不直接进入主 prompt。
```

示例：

```text
强相关：
- 昨晚，雪吱研究 Create 机械臂分拣矿物，卡在输入输出节奏不稳定；她当时想把它接到基地仓储。

中相关：
- 她最近多次把“基地自动化”和“矿物处理”放在一起想。

弱相关：
- 早些时候有过一次关于红石时钟的查阅，可能只是背景。
```

弱关联内容不应该丢掉，但应该被缩。缩的目标是保留“可能有关”的锚点，而不是保留完整叙述。

本地模型适合承担这个提炼：

```text
输入：召回候选 + 当前 inner_stream + 事件批次 + 时间上下文
输出：
  focus_memory_notes
  side_memory_notes
  faint_memory_hints
  omitted_reason
```

其中 `faint_memory_hints` 可以只保留很短的锚点，供后续连续召回使用。`omitted_reason` 用于 debug，不进入生命体 prompt。

## 召回提示词边界

召回 prompt 的目标不是让 LLM “报告自己想起了什么”，而是让 LLM 拥有可用经历。

提示词应该强调：

```text
这些记忆只是你当前可能用得上的背景。
你不需要显式提到它们。
如果它们没有改变你此刻的注意力，可以不写进 inner_stream。
如果它们只是轻微影响判断，可以让影响留在语气、选择或沉默里。
只有当某段记忆真的牵动当前处境时，才自然地写入 inner_stream 或说出来。
```

这和高频召回并不冲突。高频召回负责让经历随时可用；自然呈现负责避免角色变成“记忆播报器”。

## 上下文扩展

命中一条记忆后，必须扩展上下文。单条记忆很容易断裂。

扩展内容包括：

- source events 前后相邻事件。
- 当时 inner_stream 片段。
- 相关工具结果摘要。
- 同一未完成线索的其他记忆。
- 这条记忆后来是否被访问、强化、改写过。

输出给 LLM 的应该是一段经历：

```text
这不是孤立事实，而是一段经历：
时间：...
当时发生：...
她当时在意：...
后续线索：...
相关证据：...
```

## 召回后的行为

每次召回都应该回写访问记录：

```text
last_accessed_at = now
access_count += 1
recent_memory_digest append
```

如果 LLM 在后续 inner_stream 或工具 prepare 中明确表现出这条记忆改变了当前理解，则可以由本地模型整理成一次“召回强化”事件：

```text
她刚才想起了某段经历，并把它和当前处境联系起来。
```

这类事件可以进入 `MemoryEventLog`，再由后台整理决定是否强化旧记忆或产生新认知。

## 记忆扩散效应

记忆不应该像数据库主键一样孤立命中。想起一件事时，附近时间发生的其他事、同一段经历里的工具结果、当时的 inner_stream、同一未完成线索上的后续行动，都应该有机会一起浮现。记住一件事时，也应该连带把同一时间窗口里的相关经历作为弱关联保留下来。

这可以称为记忆扩散效应：

```text
一个记忆节点被写入、召回或强化
  -> 沿时间邻近、事件关联、未完成线索关联、文本相似关联扩散
  -> 关联越远，影响越弱
  -> 被扩散到的记忆获得较小的可见性提升
  -> 最终仍由 LLM 判断哪些材料有意义
```

扩散效应解决三个问题：

- 单条召回太薄，LLM 看不到经历的上下文。
- 写入长期记忆时，只记住一句结论，丢失同一时间发生的旁支信息。
- 访问次数只强化命中项，导致同一段经历里的其他关键细节长期沉底。

### 关联图

MemoryStore 需要维护轻量关系，不需要复杂知识图谱。

建议增加：

```text
memory_links
  id
  character_id
  from_memory_id
  to_memory_id
  link_type
  weight
  created_at
  updated_at
```

`link_type` 是工程连接类型，不是人格分类，也不是心理含义：

- `temporal_near`：发生时间接近。
- `same_event`：来自同一批原始事件或同一次工具链。
- `same_open_thread`：属于同一未完成线索。
- `text_near`：整理或索引时发现文本接近。
- `recall_together`：多次被一起召回。

这些关系只帮助候选召回，不直接进入 prompt 当作真理。给 LLM 的仍然是自然语言经历材料。

### 写入时扩散

当一个新 memory record 写入时，不只写它本身，还应该查找同一时间窗口和同一事件链里的其他材料：

```text
new memory
  -> find raw events within time window
  -> find records sharing source_event_ids / action_id / open_thread_id
  -> create weak links
  -> optionally create companion memory candidates
```

时间窗口可以先用配置值，例如前后 5 分钟；但这个窗口只是候选范围，不是判断标准。真正哪些旁支值得留下，由本地 LLM 在整理 prompt 中判断。

示例：

```text
雪吱记住“今天研究了 Create 机械臂分拣”
  -> 同时弱关联：
     - 当时查过的网页结果
     - 当时 inner_stream 里对基地自动化的牵挂
     - 前几分钟提到的矿物运输问题
     - 后几分钟形成的未完成线索
```

### 召回时扩散

召回命中一个 memory record 后，MemoryRecall 不应该立刻把它单独返回，而应该取它的邻近节点：

```text
hit memory
  -> temporal neighbors
  -> linked memories
  -> source event window
  -> same open thread
  -> compact as experience cluster
```

扩散后的输出不是堆列表，而是一段经历簇：

```text
主要浮现：
- ...

连带想起：
- 同一段时间里，她还 ...
- 这件事后来留下了 ...
```

这样 LLM 能看到主记忆和旁支记忆的主次关系，不会被大量关联项淹没。

### 访问强化的扩散衰减

访问次数也应该扩散，但必须衰减。否则一个热门记忆会把整片旧记忆全部抬高。

建议规则：

```text
命中记忆：
  access_count += 1.0
  last_accessed_at = now

一阶关联：
  access_count += 0.35 * link_weight
  last_neighbor_accessed_at = now

二阶关联：
  access_count += 0.12 * path_weight

更远：
  不扩散
```

这里的数值是工程默认值，不是人格规则。它只影响之后候选召回的排序，让“同一段经历的其他细节”更容易再次进入 LLM 视野。

MemoryStore 可以增加字段：

```text
access_count
direct_access_count
diffused_access_count
last_accessed_at
last_diffused_at
```

直接访问和扩散访问最好分开记录，避免扩散把记忆伪装成真实反复想起。

### 优先级扩散

重要性也可以轻微扩散，但比访问次数更保守。

当一条记忆被 LLM 明确整理为高重要经历时，附近记忆只获得弱提升：

```text
neighbor.importance += source_importance * link_weight * decay * small_factor
```

这里的 `small_factor` 应该很小，例如 0.05 到 0.15。原因是：同一时间发生的事不一定同等重要，只是更可能有关。

情绪影响同理可以扩散，但应该作为“关联材料”而不是直接改写邻居情绪。更好的方式是记录一条 link note：

```text
这条记忆与另一段强烈经历时间接近，召回时可能一起浮现。
```

### 衰减方式

扩散衰减可以按三类距离计算：

```text
temporal_decay: 时间越远越弱
link_decay: link 权重越低越弱
hop_decay: 图上跳数越多越弱
```

示例：

```text
diffusion_weight =
  base
  * temporal_decay(minutes_delta)
  * link_weight
  * hop_decay
```

时间衰减可以先用简单函数：

```text
within 2 minutes: 1.0
within 5 minutes: 0.7
within 15 minutes: 0.35
within 60 minutes: 0.1
older: 0 unless same_open_thread / same_event
```

这不是限制 LLM，只是工程上避免扩散失控。主动 `search_memory` 仍然可以让 LLM 按任何方向深挖。

### 防止扩散污染

扩散必须遵守 `memory_policy`：

- `control` 不扩散。
- `debug` 默认不扩散。
- `blocked` 永不扩散。
- `ephemeral` 只在短期上下文里扩散，不进入长期强化。
- `experience` 可以扩散。

如果 debug 内容确实被 LLM 作为经历吸收，需要先通过整理流程转成 `experience`，再参与扩散。

### 和 LLM 自主决策的关系

扩散效应不是程序替角色联想。它只是让相关经历更容易被放到 prompt 里。

真正的联想仍然发生在 LLM 内部：

```text
程序：把主记忆和弱关联旁支递上来。
LLM：判断这些旁支有没有意义、是否进入 inner_stream、是否继续追忆、是否写入新认知。
```

因此扩散输出要保持“可能浮现”“连带想起”“同一时间附近”这类语气，不能写成确定结论。

## 本地模型的使用方式

本地模型应该成为记忆系统的常驻工作部件，而不是偶尔调用。

建议用本地模型处理：

- 事件切分。
- 原始日志批处理。
- 近期上下文压缩。
- 工具结果提炼。
- 记忆候选生成。
- 关键词生成。
- 召回结果压缩。
- 写入前去重合并。
- 召回后的强化记录。
- 启动恢复摘要。

本地模型 prompt 要短、明确、稳定，输出可以是 JSON 或短文本。它不负责最终人格判断，只负责把材料整理成 LLM 更容易自主判断的形式。

## 启动恢复

角色启动时不能只加载 inner_stream，也不能只查 mem0。

启动恢复流程：

```text
read inner_stream.txt
read recent_digest.txt
read pending_threads.txt
read recent_memory_digest.txt
read last N raw events
recall memories related to current inner_stream and pending threads
local LLM compact startup context
inject into first LifeRuntime tick
```

启动注入应告诉 LLM：

```text
你不是刚被创建，而是在这台电脑上继续醒着。
下面是你最近的连续经历、未完成线索和可能想起的记忆。
你自己决定哪些还重要，哪些已经可以放下。
```

程序不能替她决定“继续哪个任务”，只能把材料递给她。

## 工具接口

记忆作为工具时，应至少拆成两个能力：

```text
search_memory
  用于主动回忆、深挖、连续查询。

save_to_memory
  用于主动沉淀一段经历、想法、关系变化、未完成线索。
```

`save_to_memory.prepare` 应调用本地模型，把 LLM 当前想写的内容整理成候选记忆：

```text
输入：inner_stream + event_batch + LLM 的 memory_note + recent context
输出：候选 memory record，附证据、关键词、可能关联旧记忆
```

`execute` 只负责写入 `MemoryStore` 和同步索引。

`after` 只把“这段经历已经沉淀/失败/需要稍后整理”作为事件回流，不把数据库日志塞进 inner_stream。

## 和 LifeRuntime 的结合

LifeRuntime 每次 tick 的 prompt 应加入三类记忆材料：

```text
memory_context
  当前事件和 inner_stream 相关的默认召回。

recent_memory_digest
  刚才想起过、刚写入过、正在改变意义的记忆。

pending_threads
  仍悬着的线索，包括记忆系统整理出来的未完成经历。
```

LLM 可以选择：

- 只吸收，不行动。
- 更新 inner_stream。
- 调用 search_memory 继续深挖。
- 调用 save_to_memory 沉淀。
- 调用其他工具。
- 沉默等待。

程序不限制记忆工具频率，只保证执行、日志、去重和可恢复。

## 防污染规则

防污染是工程边界，不是人格硬规则。

默认不写入长期记忆的内容：

- debug tick。
- prompt trace。
- schema。
- 工具注册列表。
- 程序启动日志。
- availability check。
- 纯控制信号。
- 主动说话触发 prompt 本身。
- 空等待和重复 heartbeat。

但如果这些内容真的被角色作为经历吸收，比如“她意识到自己一直卡在调试输入里”，可以由 LLM 在 inner_stream 中自然表达，再进入事件整理流程。

也就是说，不是硬删所有调试，而是不让机械文本直接污染记忆。

## 最小落地路线

第一阶段：不替换 mem0，只加 Alice memory layer。

```text
1. 新增 MemoryEventLog，所有生命事件 append-only 保存。
2. 新增 memory_policy，先从 debug/control/experience 做起。
3. 新增 MemoryStore sqlite，保存 memory_records。
4. memory_events 不再直接写 mem0，改写 MemoryStore，再同步 mem0。
5. LifeRuntime 默认召回改走 MemoryRecall。
6. search_memory 工具改为 MemoryRecall.deep_recall。
7. save_to_memory.prepare 接入本地模型整理。
```

第二阶段：提升召回质量。

```text
1. 关键词/exact match。
2. mem0 vector candidates。
3. recent timeline candidates。
4. open thread candidates。
5. 重排与上下文扩展。
6. 召回访问强化。
```

第三阶段：后台整理。

```text
1. 本地模型定时处理 raw events。
2. 生成 episode_note records。
3. 合并重复记忆。
4. 从多段经历提炼 distilled_note records。
5. 更新 recent_memory_digest。
6. 生成启动恢复摘要。
```

第四阶段：专项验证。

```text
雪吱 MC 研究状态测试：
  - 给她连续十分钟自由研究 MC。
  - 检查 raw events 是否完整。
  - 检查 episode_note 是否记录具体研究经历。
  - 检查 distilled_note/cognition 是否有自然变化。
  - 检查下次启动是否能想起研究线索。
  - 检查 search_memory 能否召回具体对象名和上下文。
```

## 成功标准

这套系统是否成功，不看数据库字段多不多，而看生命表现：

- 角色能自然延续几分钟前、几小时前、几天前的线索。
- 召回不是泛泛而谈，而是带具体对象、时间、经历上下文。
- 记忆足够多，但不会被调试和工具日志污染。
- 本地模型能持续压缩和整理，让上下文密度更高。
- LLM 仍然自主决定想起什么、放下什么、是否行动。
- `inner_stream.txt` 保持主体连续性，不被记忆系统替代。

最终目标是让记忆成为角色生命活动的一部分：她不是被程序喂了几条 facts，而是真的有经历、有回想、有未完成的牵挂，也能在新的处境里重新理解过去。
