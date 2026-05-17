# STT

主要模块：

- `kokoro/stt.py`
- `kokoro/conversation.py`

## 作用

- 麦克风采集
- 流式识别
- partial / final 输出
- 交给上层做 barge-in 与多角色 user_turn

## 当前相关问题

- 多角色模式下 STT 保持在线，不因 TTS 自动暂停
- 回声处理主要依赖 AEC
- 文本层还有额外的“疑似 TTS 回声过滤”
