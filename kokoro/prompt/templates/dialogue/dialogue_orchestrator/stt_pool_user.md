当前时间：
{{ timestamp }}

场景：
这是{{ user_name }}和{{ name }}的一对一角色对话。STT 池是{{ user_name }}刚才连续说出的语音识别文本，可能包含半句话、停顿、错字、重复或多个话题。

角色资料：
{{ profile }}

最近对话：
{{ recent_history }}

认知上下文：
{{ cognition }}

内在叙事流：
{{ inner_stream }}

额外上下文：
{{ extra_context }}

当前 STT 池：
{{ pool_text }}

请只返回一个 JSON 对象。
