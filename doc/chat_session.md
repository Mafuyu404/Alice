# 会话与人格层

`kokoro/chat_session.py` 负责单角色会话。

## 主要职责

- 组装 system prompt
- 维护历史摘要
- 注入长期记忆
- 注入 cognition / emotion
- 在回复后触发记忆写入

## 输入材料

- 角色设定
- 最近历史
- 长期记忆
- cognition
- emotion
- 屏幕/网页上下文（由调度器决定是否注入）

## 输出

- 当前角色的一次回复

## 注意

- ChatSession 只负责“这个角色如何说”
- 什么时候说、要不要说，由调度器决定
