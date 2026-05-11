# 状态机

`kokoro/state_machine.py` 是完整 CLI 的运行状态中心。它用于协调 STT、LLM、TTS、主动搭话、错误恢复和关闭流程。

`text_cli.py` 不使用状态机。

## 主要状态

系统状态大致包括：

- idle
- listening
- thinking
- speaking
- screen watching
- error
- shutdown

同时还有 STT、TTS、主动搭话等子状态。

## 作用

状态机负责：

- 防止 STT、LLM、TTS、impulse 互相抢占。
- 判断用户是否正在说话。
- 处理 barge-in。
- 在错误后恢复。
- 在关闭时通知后台线程退出。

## 使用位置

主要在 `cli.py`：

- 初始化 `SystemStateMachine`
- STT 线程根据语音开始/结束发事件
- 对话处理线程进入 thinking / speaking
- impulse 主动搭话前抢占会话槽
- error recovery worker 根据 ERROR 状态恢复

## 调试

如果出现“主动搭话不说话”“TTS 卡住”“STT 不响应”，优先检查：

- 当前状态是否一直 busy
- TTS 是否一直 `is_playing`
- 是否有异常把状态切到 ERROR
- 是否正确发出 `TTS_DONE`

状态机的目标是让完整模式可控；精简文字模式绕过它以减少测试变量。

## ConversationPhase（新增）

引入 `ConversationalPhase` 枚举作为对话层面的状态描述，与 `SystemState` 并行：

| Phase | 含义 |
|-------|------|
| IDLE | 无人说话，无待处理输出 |
| USER_SPEAKING | 用户持有话轮（STT 产出 partial） |
| USER_PAUSED | 用户暂停但未结束话轮 |
| AI_THINKING | LLM 生成中 |
| AI_SPEAKING | AI 持有话轮（TTS 播放中） |
| OVERLAP | 用户和 AI 同时说话 |
| WAITING | AI 等待响应当中 |

与 `SystemState` 的区别：
- SystemState = **系统忙不忙**（IDLE / THINKING / SPEAKING / ERROR）
- ConversationalPhase = **谁在说话、话轮到哪了**

`set_conversation_phase()` 通过 `subscribe_phase` 通知观察者。
