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
