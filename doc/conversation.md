# ConversationManager — 自然对话调度

取代旧的 `kokoro/pool.py`（ConversationPool），实现事件驱动的自然对话管道。

## 架构

```
麦克风音频 → AEC → ConversationManager.feed_audio()
                        │
                  STT 流式识别
                        │
              ┌─────────┴─────────┐
              │                   │
         partial 更新          endpoint 触发
              │                   │
        重叠分类器           交付最终文本
        (0.5B模型)               │
              │               LLM → TTS
    continue / soft_break /
    hard_break
```

关键特性：
- 实时 partial 输出，不等静默端点
- 重叠说话时 0.5B 模型分类打断级别
- `on_user_utterance` 回调在 STT 线程中只做快速打断信号，LLM + TTS 派发到工作线程
- STT 线程始终空闲，用户可随时再次开口

## 重叠分类

`kokoro/overlap.py` — 轻量分类器，通过 Ollama 调用 0.5B 模型。

输入：用户插话文本 + AI 正在说的内容
输出：continue（不打断）/ soft_break（播完当前块再停）/ hard_break（立即停）

缓存：相同文本在 300ms 内不重复调用模型。

## 打断级别

软打断（`tts_engine.soft_interrupt()`）：
- 播放线程播完当前音频块后停止
- 不关闭 WebSocket 连接
- 不丢弃音频队列中的剩余数据

硬打断（`tts_engine.interrupt()`）：
- 立即停止播放
- 清空音频队列
- 关闭 WebSocket

## 与旧系统的区别

| 项目 | 旧（pool.py） | 新（conversation.py） |
|------|-------------|-------------------|
| 文本交付时机 | 等静默 0.7s + LLM 精炼 | endpoint 即时交付 |
| 重叠处理 | 用户一开口就硬打断 | 模型判决（3 级） |
| STT 线程阻塞 | 整个对话周期阻塞 | 仅快速信号处理 |
| 状态描述 | SystemState 单一维度 | + ConversationalPhase |
