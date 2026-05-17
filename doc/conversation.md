# 对话输入层

实现文件：

- `kokoro/conversation.py`

## 作用

负责把流式 STT 输入组织成“用户这次说完了一句话”。

## 能力

- partial 文本回调
- endpoint 检测
- barge-in / overlap 分类
- 交给上层决定是否打断 TTS

## 说明

对话输入层不负责 LLM 回复，只负责产出用户话语事件。
