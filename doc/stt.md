# STT

主要模块：
- `kokoro/stt.py`
- `kokoro/conversation.py`
- `kokoro/aec.py`
- `kokoro/dialogue_orchestrator.py`
- `cli.py`

## 职责

- 采集麦克风音频。
- 运行流式语音识别。
- 把 partial 文本展示到 STT 字幕和终端。
- 处理 AEC 后的音频卫生。
- 在单人语音模式下维护 STT 文本池，并把发言时机交给 Dialogue LLM 判断。

## Dialogue STT 池

单人语音模式默认启用 `stt_dialogue_pool_enabled = true`。

这意味着 STT 不再把“截出来的一句”直接推送给对话层。流程改为：

1. `ConversationManager` 监听流式识别结果，只产出“可能可以评估”的文本片段。
2. `cli.py` 把片段合并进 STT 池。
3. 明显停顿后，CLI 把当前 STT 池交给 `DialogueOrchestrator.decide_stt_pool_turn()`。
4. Dialogue LLM 一次返回 `action`、`consumed_text`、`remaining_text`、`reply`。
5. `action = wait` 时，不显示 `[User]`，不写入 history，不触发 TTS，只把文本留在池里继续等。
6. `action = speak/backchannel` 时，才把 `consumed_text` 当作本轮用户输入，写入 history，并播放 `reply`。

这套机制的目标是避免把“我说……我给你……新功能……”或数数测试拆成多轮，也避免角色对半句话抢答。

## Dialogue 输出约定

`decide_stt_pool_turn()` 要求 LLM 返回 JSON：

```json
{
  "action": "wait|backchannel|speak",
  "consumed_text": "本次真正回应并写入历史的用户内容",
  "remaining_text": "还没回应、需要留在 STT 池里的内容",
  "reply": "角色直接说出口的话",
  "notes": "简短判断理由"
}
```

`consumed_text` 是 STT 池的提炼结果，不要求逐字等于 ASR 原文。`remaining_text` 用于保留一口气说出的后续话题，避免丢内容或重复回应。

## 端点层

底层 `ConversationManager` 仍保留静音端点候选，但端点不再等价于用户轮次。它只负责把可能稳定的识别片段送入上层池。

相关参数：
- `stt_refine_stable_seconds = 0.9`：识别文本稳定且麦克风静音达到此时间后，认为出现一个端点候选。
- `stt_utterance_commit_seconds = 0.55`：端点候选还要再等一小段时间才进入池。
- `stt_short_utterance_extra_seconds = 1.4`：短片段更可能是半句话，额外等待。
- `stt_short_utterance_max_chars = 8`：长度不超过 8 的片段走短片段等待。
- `stt_turn_merge_seconds = 1.4`：CLI 收到一个或多个 STT 片段后，先合并再触发 Dialogue 池评估。
- `stt_dialogue_pool_enabled = true`：启用 Dialogue 统一处理 STT 池；设为 `false` 可回退旧路径。

## AEC 与回灌

STT 默认依赖 AEC 处理外放回声，而不是在 TTS 播放期间完全暂停麦克风。

当前推荐参数：
- `tts_volume = 1.25`：避免 TTS 过响导致扬声器到麦克风的非线性失真。
- `[aec].delay_ms = 85`：适合 Windows + 流式 TTS + 声卡缓冲的常见桌面链路。
- `[aec].ns_level = 3`：更激进地压低 AEC 后残留的 TTS 碎片。
- `[aec].auto_reset_on_tts_done = true`：每轮 TTS 完成后重置自适应滤波状态。

AEC 输出后仍经过 `kokoro.stt.denoise()`，用于去除 DC/低频噪声并门控极低能量残留。这个处理只影响音频输入卫生，不参与对话决策。

## 调参方向

- 如果 TTS 仍偶发进入 STT，先把 `tts_volume` 降到 `1.0`，再尝试 `delay_ms = 95` 或 `105`。
- 如果用户插话被压得太厉害，把 `[aec].ns_level` 从 `3` 降到 `2`。
- 如果听起来像双份/错位回声，把 `delay_ms` 每次降低 `10ms`。
- 如果残留回声清晰但不同步，把 `delay_ms` 每次提高 `10ms`。
- 如果 Dialogue 经常等太久，把 `stt_turn_merge_seconds` 降到 `1.0`。
- 如果仍然拆句或抢答，把 `stt_turn_merge_seconds` 提到 `1.8`，或提高 `stt_short_utterance_extra_seconds`。
