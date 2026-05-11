# STT

STT 负责完整 CLI 的麦克风语音识别。`text_cli.py` 不使用 STT。

## 配置

```toml
stt_model_dir = "models/stt"
stt_refine_model = "qwen2.5:1.5b"
stt_refine_mode = "inline"
stt_refine_stable_seconds = 0.7
stt_pool_tick_seconds = 0.05
stt_refine_max_tokens = 128
stt_skip_short_refine = true
stt_skip_short_refine_max_chars = 18
stt_pause_during_tts = true
```

## 流程（新架构）

1. `cli.py` 打开麦克风。
2. `kokoro/stt.py` 用 sherpa-onnx 做流式识别。
3. `kokoro/conversation.py`（ConversationManager）接管 STT 流，实时输出 partial 结果。
4. 如果用户说话时 AI 也在说（重叠），调用 `kokoro/overlap.py`（0.5B 模型）判断打断级别。
5. 用户说完（endpoint）或模型判定需要打断时，文本进入 ChatSession。

旧 `kokoro/pool.py`（ConversationPool）已被 ConversationManager 取代。

## 重叠说话

当用户和 AI 同时说话时，系统不再简单地硬打断。而是通过 `overlap_model`（默认 qwen2.5:0.5b）判断：

- **continue** — 用户在附和（嗯/对/啊），AI 继续说。
- **soft_break** — 用户开始说实质性内容，AI 播完当前音频块后让出话轮。
- **hard_break** — 用户紧急打断，AI 立即停止。

所有决策由模型驱动，没有硬编码阈值。

配置：

```toml
overlap_model = "qwen2.5:0.5b"
```

## 精炼模式

`separate`：

- （旧模式）独立调用小模型修正 STT 文本。
- 在新 ConversationManager 中不再使用，保留兼容。

`inline`：

- 本地正则清洗。
- 聊天 LLM 在回复时隐式纠错。
- 延迟低。

`none`：

- 只做本地正则清洗。
- 不额外调用 LLM。

## 设备

列出麦克风设备：

```bash
python cli.py --list-devices
```

指定设备：

```bash
python cli.py --device 1
```

## TTS 播放期间暂停麦克风

不再推荐。配合 AEC + ConversationManager，STT 和 TTS 可以同时运行：

```toml
stt_pause_during_tts = false
```
