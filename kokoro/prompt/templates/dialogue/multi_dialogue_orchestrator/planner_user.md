场景：
{{ user_name }}和两个独立角色正在进行三角色对话。请从第三人称旁观视角判断下一步场景动作。
{{ user_name }}不是命令发出者，也不是需要两个角色轮流服务的对象；{{ user_name }}只是场上的一名参与者。

当前时间：
{{ timestamp }}

事件：
类型：{{ event_type }}
说话者：{{ speaker }}
来源角色 id：{{ source_id }}
内容：{{ event_text }}

事件理解提示：
- user_utterance 表示{{ user_name }}刚说了一句话。优先判断哪位角色最适合直接接住；如果后续仍有自然余波，另一位角色可以稍后补一句，但不是必须。
- character_utterance 表示某个角色刚说完，下一步可以由另一个角色接话，也可以沉默。
- idle_tick 表示场景进入空白，不是请求补答上一句话；若最近已经自然收束，优先 silence。

角色列表：
{{ characters }}

最近共享对话：
{{ recent_history }}

各角色运行时上下文：
{{ runtime_context }}

待执行计划：
{{ pending_plans }}

额外上下文：
{{ extra_context }}

只返回一个 JSON 对象。
