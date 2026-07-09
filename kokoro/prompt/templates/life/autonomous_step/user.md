角色：{{ name }}
触发原因：{{ trigger_reason }}
可用动作：{{ capabilities }}

【内在叙事流】
{{ inner_stream }}

【认知上下文】
{{ cognition_context }}

【相关记忆】
{{ memory_context }}

【最近对话】
{{ recent_history }}

【最近事件】
{{ events }}

【外部现场】
{{ qq_packets }}

【可用表情包】
{{ sticker_candidates }}

{{ silent_streak }}

请选择下一步一个自然行动。
- action 必须来自【可用动作】。
- args 只填写该 action 真正需要的字段；不知道字段时，优先用最小必要信息表达意图。
- 公开表达类行动只写真正说出口或发出去的内容，不要加括号旁白。
- 候选材料只能在确实合适时使用，不合适就不要强用。
- 等待或旁听是有效选择，但应来自真实的现场节奏和内在判断，不要变成反复自责或默认停滞。

判断时先看外部现场里的时间、说话者、话轮关系和候选材料。正文里出现的名字不等于说话者；反馈是经验，不是禁言。JSON：
