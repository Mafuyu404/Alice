# 内在叙事流

实现文件：`kokoro/inner_stream.py`  
运行文件：`characters/{character_id}/inner_stream.txt`

内在叙事流是角色的自我连续性核心。它维护一段自然语言文本，用来描述角色此刻还在延续的心境、注意力、疑惑、表达倾向、未完成感和克制边界。

它不是：兴趣权重表、计划表、可解析规则、长期人格设定、事实档案。

## 输入-决策-输出结构

内在叙事流的本质是一个轻量的输入-决策-输出层：

- 输入：人的话语、QQ 群消息、搜索结果、屏幕/网页、记忆、时间流逝、自己的发言和沉默。
- 决策：由 LLM 在内在叙事流中形成注意、兴趣、疑惑、克制、未完成感和行动倾向。
- 输出：搜索、QQ 发言、继续旁听、沉默、整理记忆、后续主动对话等。

程序只负责承载输入、执行可用输出、再把结果作为新输入反馈回来；不应该用固定规则替角色决定“搜索后必须发言”或“某类群消息必须记忆”。

## 与现有层的关系

- `memory`：记录发生过什么，包括对话事件、QQ 经历、自主搜索和自身行动。
- `cognition`：记录长期稳定认知，例如对人、项目、关系和自我的判断。
- `emotion`：维护浅层情绪基调和中期动机。
- `inner_stream`：维护“我现在作为我还在想什么、想靠近什么、想避开什么、什么没完成”。
- `dialogue_orchestrator` / `QQAutonomousParticipant`：读取 inner stream 后判断是否说话。
- `inner_stream_search`：读取 inner stream 后判断是否搜索。
- `inner_memory`：读取 inner stream 和最近事件后判断是否形成值得长期记住的一整件经历。

## 更新时机

`ChatSession` 会把对话、QQ 环境包、搜索结果、自身行动、时间节奏等统一发布为 input events。`InnerStreamLoop` 按节奏合并事件并重写内在叙事流。

QQ 环境中，群消息会先进入内在叙事流并被吸收，然后再由 QQ 发言判断读取更新后的 inner stream 决定是否发言。

## 主动搜索

`[inner_stream_search]` 会在内在叙事流更新后，让 LLM 根据当前内在叙事流自行判断是否要搜索。搜索不是普通问答工具调用，而是角色自己的认知动作；搜索结果会作为 `web_search` 外部输入事件反馈回内在叙事流。

```toml
[inner_stream_search]
enabled = true
base_url = "http://127.0.0.1:3000"
model = ""
max_results = 5
max_event_chars = 6000
timeout = 45.0
```

使用前需要启动 open-webSearch 本地 daemon，并确保端口和 `base_url` 一致。

## 记忆反思

`[inner_memory]` 会在内在叙事流更新后，让 LLM 判断最近输入和自身行动是否已经形成一件值得记住的经历。

记忆以自然语言事件写入，描述清楚发生地点、人物、经过、角色自己的搜索/发言/沉默和理解变化，不做人工索引。

```toml
[inner_memory]
enabled = true
model = ""
max_tokens = 512
```

好的记忆示例：

```text
我在某个 QQ 群旁听到几个人讨论 open-webSearch 的本地 daemon 端口问题，自己搜索后确认文档示例和默认端口可能不同，因此之后更注意区分示例配置和包默认值。
```

## 设计原则

- 程序只读写和注入文本，不解析 inner stream 内容做 if/else。
- LLM 负责重写内在叙事流，并在搜索、发言、记忆等输出判断中读取它。
- 内在叙事流可以影响调度，但不是命令。
- 搜索、发言、沉默和记忆都是角色生活的一部分，不是用户请求工具链。
- 如果没有明显变化，可以保留旧流并轻微修正，不要清空。
