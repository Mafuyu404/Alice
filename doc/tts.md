# TTS

TTS 由 `kokoro/tts.py` 动态分发到具体后端：

- `kokoro/tts_minimax.py`
- `kokoro/tts_cartesia.py`

## 通用配置

```toml
tts_backend = "minimax"
tts_volume = 1.0
tts_stream_chunk_chars = 28
tts_stream_sentence_min_chars = 8
```

`tts_volume` 是播放音量倍率：

- `0` 静音
- `0.5` 一半音量
- `1.0` 原始音量
- `2.0` 最大倍率，可能削波失真

音量是在写入声卡前对 `float32` 音频数组处理，因此 MiniMax 和 Cartesia 都生效。

## MiniMax

```toml
tts_backend = "minimax"
minimax_api_key = ""
minimax_model = "speech-2.8-turbo"
minimax_sample_rate = 32000
minimax_tts_speed = 1.1
minimax_tts_buffer_seconds = 0.3
```

MiniMax 后端使用 WebSocket 流式合成。`StreamingTTS` 会维护长连接、预缓冲音频并自动重连。

## Cartesia

```toml
tts_backend = "cartesia"
cartesia_api_key = ""
tts_voice_id = ""
tts_sample_rate = 24000
```

Cartesia 后端按句播放，适合备用或不同声音风格测试。

## CLI 控制

关闭 TTS：

```bash
python cli.py --no-tts
```

`text_cli.py` 永远不启动 TTS。

## 常见问题

音量太大：

```toml
tts_volume = 0.5
```

播放期间麦克风误收音：

```toml
stt_pause_during_tts = true
```

首音延迟高：

- 减小 `minimax_tts_buffer_seconds`
- 减小 `tts_stream_chunk_chars`
- 检查网络和 TTS 服务响应
