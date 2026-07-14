角色：{{ name }}

行动意向：
{{ action_intent }}

最近进入的信息：
{{ event_batch }}

最近已发送表达：
{{ recent_self_expressions }}

可用能力：
{{ tool_capabilities }}

请把行动意向翻译成 action_plan。只使用可用能力里逐字列出的 action name。

如果需要参数，所有参数都必须写进该 action 的 args object。不要把参数写在 args 外面。

如果行动意向还不够具体，或者没有对应能力，输出 notes，不要发明能力。
