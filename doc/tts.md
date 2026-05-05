# 语音合成 (TTS)

## 架构

TTS 子系统采用动态后端加载机制：

```
回复文本 → tts.py 调度层 → tts_minimax.py / tts_cartesia.py → 扬声器
```

`kokoro/tts.py` 根据 `config.toml` 中的 `tts_backend` 动态导入后端模块。模块内的函数（如 `StreamingTTS`、`warmup`、`get_voices`）通过 `__getattr__` 直接透出，对调用方透明。

## MiniMax 后端 (tts_minimax.py)

国内可用，WebSocket 流式合成，首音延迟低。

### 特性
- WebSocket 流式合成，支持自动重连
- 音频格式：PCM 16-bit 单声道，采样率 32000 Hz（`minimax_sample_rate` 可配置）
- 支持语速控制（`minimax_tts_speed`，默认 1.05）
- 预缓冲机制：累积 `minimax_tts_buffer_seconds`（默认 0.3s）的音频再开始播放，减少网络抖动造成的卡顿

### 类：`StreamingTTS`

| 方法/属性 | 功能 |
|------|------|
| `push(text)` | 将 LLM 流式文本推入缓冲区，自动检测句末标点并发送完整句子 |
| `end_sentence()` | 强制刷新缓冲区中剩余文本，等待所有待处理句子播放完毕 |
| `prepare()` | 启动音频输出流和 WebSocket 接收线程，等待 task_started 确认 |
| `flush()` | 同 `end_sentence()` |
| `close()` | 停止播放、关闭 WebSocket 和音频流 |
| `is_playing` | 属性，当前是否有音频正在播放或队列非空 |

### 工作流程

1. `warmup()` — 检查 API Key 是否配置
2. `StreamingTTS()` 初始化 — 配置音色、语速、缓冲参数
3. `prepare()` — 建立 WebSocket 连接，发送 `task_start`，等待 `task_started` 确认
4. LLM 流式输出过程中，逐段调用 `push(text)`
5. `push()` 内部检测句末标点（`[。！？!?；;]`），匹配到则通过 WebSocket 发送 `task_continue`
6. WebSocket 接收线程持续接收音频 chunk → 解码 → 放入 `_audio_queue`
7. 播放线程从队列取音频，经预缓冲后写入 `sounddevice.OutputStream`
8. `is_playing` 在预缓冲阶段或队列非空时返回 `True`
9. `end_sentence()` 发送剩余文本，等待所有音频播放完毕

### 重连机制

WebSocket 意外断开时：
- `_ws_started` 标记清除
- `push()` 中的文本保留在 `_buf` 中，不丢失
- 接收线程自动重连并重新发送 `task_start`
- 重连成功后，缓冲区的句子重新发送

### 其他函数

| 函数 | 功能 |
|------|------|
| `text_to_speech_stream(text, voice, speed)` | 同步流式合成，返回 `(audio_ndarray, sample_rate)` 生成器 |
| `text_to_speech(text, voice, speed)` | 同步合成完整 WAV 音频 |
| `play_tts(text, voice, speed, blocking)` | 合成并播放 |
| `enqueue_tts(text, voice, speed)` | 入队播放（后台线程顺序播放） |
| `stop_playback()` | 停止播放（当前为空实现） |
| `warmup()` | 预加载，检查 API Key |
| `get_voices()` | 返回可用音色列表和引擎信息 |

### 流式分句

`push()` 内部逻辑：

- 累积字符到 `_buf`
- 句末标点匹配时，取标点之前的完整句子发送
- 剩余部分保留在缓冲区，等待后续 `push()` 补全
- WebSocket 不可用时保留文本不丢，等待重连

## Cartesia 后端 (tts_cartesia.py)

海外 TTS 服务，SSE 流式合成。

### 特性
- SSE (Server-Sent Events) 流式合成
- 采样率：24000 Hz（`tts_sample_rate` 可配）
- 多线程播放，每个句子独立线程处理

### 类：`StreamingTTS`

| 方法/属性 | 功能 |
|------|------|
| `push(text)` | 将 LLM 流式文本追加到缓冲区 |
| `end_sentence()` | 将缓冲区文本作为一个完整句子发送合成，启动独立播放线程 |
| `prepare()` | 空操作（兼容接口） |
| `flush()` | 同 `end_sentence()` |
| `close()` | 设置停止标记 |
| `is_playing` | 属性，当前是否有音频正在播放或待播放 |

### 工作流程

1. `push(text)` 累积文本到 `_pending_buf`
2. `end_sentence()` 将缓冲区文本合并，启动后台线程调用 `text_to_speech_stream()`
3. 后台线程通过 Cartesia SDK WebSocket 获取音频 chunk
4. 播放线程逐个播放音频块

### 其他函数

| 函数 | 功能 |
|------|------|
| `text_to_speech_stream(text, voice, speed)` | 通过 Cartesia SDK 流式合成音频 |
| `text_to_speech(text, voice, speed)` | 同步合成完整 WAV |
| `play_tts(text, voice, speed, blocking)` | 合成并播放 |
| `enqueue_tts(text, voice, speed)` | 入队顺序播放 |
| `stop_playback()` | 停止播放（当前为空实现） |
| `warmup()` | 检查 API Key |
| `get_voices()` | 返回可用音色列表 |

## 播放控制

- 各模块（proactive 调度器、STT 暂停逻辑、CLI 主循环）通过 `is_playing` 检查播放状态
- CLI 主循环在用户消息处理完成后等待 `is_playing` 变为 `False`，然后调用 `scheduler.record_tts_end()` + `prepare()` 重置状态
- 主动搭话等待 TTS 播放完毕后再调用 `record_tts_end()`

## 配置

主要配置项（详见 [config.md](config.md)）：

| 配置项 | 说明 |
|------|------|
| `tts_backend` | 后端选择：`"minimax"` / `"cartesia"` |
| `minimax_api_key` | MiniMax API 密钥 |
| `minimax_model` | MiniMax 模型名（默认 `speech-2.8-turbo`） |
| `minimax_sample_rate` | MiniMax 采样率（默认 32000） |
| `minimax_tts_speed` | 语速倍率（默认 1.05） |
| `minimax_tts_buffer_seconds` | 预缓冲秒数（默认 0.3） |
| `cartesia_api_key` | Cartesia API 密钥 |
| `tts_voice_id` | Cartesia 语音 ID |
| `tts_sample_rate` | Cartesia 采样率（默认 24000） |
| `tts_stream_chunk_chars` | 流式累积字符阈值（默认 28） |
| `tts_stream_sentence_min_chars` | 最小句子长度（默认 8） |
