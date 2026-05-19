# TTS 语音合成

## 支持的后端

| 后端 | 实现文件 | 特点 |
|---|---|---|
| MiniMax | `kokoro/tts_minimax.py` | 国内直连，WebSocket 流式，低延迟，推荐 |
| Cartesia | `kokoro/tts_cartesia.py` | 海外服务，需要海外网络 |

切换方式：`config.toml` 中的 `tts_backend` 字段，导入时自动加载 `kokoro/tts_{后端名}.py`。

## 流式播放

TTS 与 LLM 流式输出并行：

```text
LLM 逐 token 输出
  │
  ▼
tts_engine.push(content)  ← 每收到一段文本就 push
  │
  ├─ 累积到内部缓冲区
  ├─ 按句末标点或字符数阈值切句
  └─ 达到刷新条件 → 发送到 TTS API → 播放
```

### 刷新控制

```toml
tts_stream_chunk_chars = 28        # 累积多少字符强制刷新
tts_stream_sentence_min_chars = 8  # 触发句子刷新的最小字符数
```

`chunk_chars` 较小 → TTS 更快出声（但断句可能不自然）
`chunk_chars` 较大 → 等待更完整句子（首音延迟更大）

### MiniMax 缓冲区

```toml
minimax_tts_buffer_seconds = 0.3   # 播放前预缓冲秒数
minimax_tts_speed = 1.1            # 语速倍率
```

`buffer_seconds` 是延迟与流畅度的权衡：
- 敏感场景：0.05 ~ 0.15
- 流畅优先：0.3 ~ 0.5

## 多角色 TTS

多人模式下，所有角色共享一个全局 TTS 锁（`_tts_lock`），串行播放：

```text
角色 A 说话 → TTS 推流 → 等待播放完成
                           ↓
角色 B 说话 → TTS 推流 → 等待播放完成
```

当前不支持多角色同时说话。这是有意设计——避免两人同时开口时的混乱感。

## 常见问题

### TTS 不出声
1. 确认 API key 已配置
2. 确认网络能连接到 TTS 服务
3. 尝试 `--no-tts` 确认问题在 TTS 侧
4. 检查音量配置 `tts_volume` 是否 > 0

### 声音断续
- 增大 `minimax_tts_buffer_seconds`
- 检查网络延迟
- 降低 `tts_stream_chunk_chars` 让每段音频更短

### 首音延迟太长
- 减小 `minimax_tts_buffer_seconds`
- 减小 `tts_stream_chunk_chars`
- 增大 `tts_stream_sentence_min_chars`
