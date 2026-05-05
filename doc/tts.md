# 语音合成 (TTS)

## 架构

TTS 子系统采用动态后端加载机制：

```
回复文本 → tts.py 调度层 → tts_minimax.py / tts_cartesia.py → 扬声器
```

`kokoro/tts.py` 根据 `config.toml` 中的 `tts_backend` 动态导入后端模块。模块内的函数（如 `StreamingTTS`、`warmup`）通过 `__getattr__` 直接透出。

## MiniMax 后端 (tts_minimax.py)

### 特性
- WebSocket 流式合成，首音延迟低
- 支持语速控制（`minimax_tts_speed`）
- 音频格式：PCM 16-bit 单声道，采样率可配置

### 类：`StreamingTTS`

| 方法 | 功能 |
|------|------|
| `prepare()` | 准备/重置播放状态，清空缓冲区 |
| `synthesize(text, rate=1.0)` | 同步合成完整音频返回 bytes |
| `push(text)` | 将文本推入流式缓冲区 |
| `end_sentence()` | 触发缓冲区中的未刷新句子开始合成 |
| `stream(text, rate=1.0)` | 异步生成器，逐段产出音频 chunk |
| `is_playing` | 属性，当前是否有音频正在播放 |
| `stop_playback()` | 停止当前播放 |
| `close()` | 关闭 WebSocket 连接 |

### 工作流程

1. `warmup()` — 预加载依赖和配置
2. `StreamingTTS()` 初始化 — 建立 WebSocket 连接
3. LLM 流式输出过程中，逐段调用 `push(text)`
4. 检测到句末标点或达到字符阈值时，调用 `end_sentence()` 刷新缓冲区
5. 音频帧通过后台播放线程送入扬声器
6. `is_playing` 控制播放状态，其他模块据此避免冲突

### 流式分句

TTS 不等待 LLM 完整输出，而是在流式过程中判断：

- 累积字符 ≥ `tts_stream_chunk_chars`（默认 28）→ 强制刷新
- 出现句末标点（。！？!?；，,）且长度 ≥ `tts_stream_sentence_min_chars`（默认 8）→ 刷新

### 缓冲机制

`minimax_tts_buffer_seconds`（默认 0.15）：预缓冲音频再开始播放，减少网络抖动造成的卡顿。

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

- 后台线程管理播放队列，与 LLM 流式输出同步
- 各模块（proactive 调度器、STT 暂停逻辑）通过 `is_playing` 检查播放状态
- `stop_playback()` 立即静音，用于中断当前回复
