# 对话输入层

实现文件：
- `kokoro/conversation.py`
- `kokoro/dialogue_orchestrator.py`
- `cli.py`

## 当前职责

对话输入层不再把流式 STT 强行切成“用户说完的一句话”。

现在的职责分为两层：
- `ConversationManager`：处理流式 STT partial、静音端点候选、barge-in / overlap、中断 TTS。
- `cli.py` 的 STT 池：累积多个 STT 片段，等待停顿后交给 Dialogue。
- `DialogueOrchestrator`：在单次 LLM 调用里判断是否该回复、提炼用户输出、保留剩余池内容，并生成回复。

## 单人语音流程

1. 麦克风音频经过 AEC 和 denoise。
2. `ConversationManager.feed_audio()` 推进流式识别。
3. partial 文本只用于字幕、终端显示和 overlap 判断。
4. 静音端点候选出现后，文本片段进入 CLI 的 STT 池。
5. STT 池到达评估窗口后，调用 `DialogueOrchestrator.decide_stt_pool_turn()`。
6. Dialogue 返回：
   - `wait`：继续等，不写 history。
   - `backchannel`：轻回应，写入 consumed 用户内容。
   - `speak`：正式回应，写入 consumed 用户内容。
7. `remaining_text` 回填到 STT 池，等待后续继续评估。

## 为什么这么做

真实对话不是一句一句精确分割的。用户可能会：
- 半句话停顿。
- 改口。
- 数数或测试识别。
- 连续说多个话题。
- 一边想一边补充。

如果程序用静音秒数直接截句，就会在“响应快”和“不打断”之间反复摇摆。当前设计把“是否该接话”交给 Dialogue LLM，让发言时机、用户意图提炼和角色回复成为同一次认知动作。

## 边界

- 多角色语音模式暂时仍保留旧的合并后推送路径。
- overlap / barge-in 仍由 `ConversationManager` 处理，因为它需要低延迟打断 TTS。
- AEC、denoise、字幕显示属于音频输入卫生和 UI，不参与对话决策。
