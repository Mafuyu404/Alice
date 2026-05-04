# 语音合成 (TTS)

## 架构

TTS 子系统采用动态后端加载机制：

```
回复文本 → TTS Dispatcher → MiniMaxBackend / CartesiaBackend → 扬声器播放
```

`kokoro/tts.py` 根据 `config.toml` 中的 `tts_backend` 动态导入后端模块：

```python
backend_module = importlib.import_module(f"kokoro.tts_{backend_name}")
tts_instance = backend_module.StreamingTTS(...)
```

## MiniMax 后端 (tts_minimax.py)

### 特性
- WebSocket 流式合成，延迟低
- 支持语速控制（`minimax_tts_speed`）
- 音频格式：PCM 16-bit 单声道
- 采样率：32000 Hz（可配置）

### 类：`StreamingTTS`

| 方法 | 功能 |
|------|------|
| `synthesize(text, rate=1.0)` | 同步合成完整音频返回 bytes |
| `stream(text, rate=1.0)` | 异步生成器，逐段产出音频 chunk |
| `close()` | 关闭 WebSocket 连接 |

### 工作流程

1. 建立 MiniMax TTS WebSocket 连接
2. 发送带 API 密钥和语音 ID 的身份验证帧
3. 发送文本帧请求合成
4. 接收音频帧，检测句尾停顿后产出完整句子音频
5. 播放线程将音频送入扬声器

### 缓冲机制

`minimax_tts_buffer_seconds = 0.3`：预缓冲 0.3 秒音频再开始播放，减少卡顿。

## Cartesia 后端 (tts_cartesia.py)

### 特性
- SSE (Server-Sent Events) 流式合成
- 采样率：24000 Hz

### 类：`StreamingTTS`

| 方法 | 功能 |
|------|------|
| `synthesize(text)` | 同步合成完整音频 |
| `close()` | 关闭会话 |

## 播放控制

- 背景线程管理播放队列
- `tts.is_playing()` 检查是否正在播放
- `tts.stop_playback()` 立即停止播放
- 各模块（如 proactive 调度器）可通过 `is_playing()` 避免在 TTS 播放时冲突
