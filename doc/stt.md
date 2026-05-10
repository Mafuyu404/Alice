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

## 流程

1. `cli.py` 打开麦克风。
2. `kokoro/stt.py` 用 sherpa-onnx 做流式识别。
3. `kokoro/pool.py` 聚合识别片段。
4. 文本稳定后进入精炼流程。
5. 精炼后的文本进入 ChatSession。

## 精炼模式

`separate`：

- 独立调用小模型修正 STT 文本。
- 质量较高。
- 延迟较高。

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

推荐开启：

```toml
stt_pause_during_tts = true
```

这样能避免扬声器声音被麦克风重新识别。
