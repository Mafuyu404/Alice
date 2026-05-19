# 对话输入层

实现文件：`kokoro/conversation.py`

## 作用

接收流式 STT 音频，组织成"用户这次说完了一句话"的事件。

**不负责**：LLM 回复、TTS、中断逻辑的执行。这些都是 cli.py 的工作。

## 架构

```text
麦克风音频 (sounddevice InputStream)
  │
  ▼
AEC 回声消除 (aec.py) → denoise (stt.py)
  │
  ▼
ConversationManager.feed_audio(chunk)
  │
  ├─ → sherpa-onnx 流式识别器
  │
  ├─ partial 文本回调 (on_partial)
  │   ├─ 更新 STT 字幕
  │   └─ 检查重叠分类 (_check_overlap)
  │       └─ continue / soft_break / hard_break
  │
  └─ 端点检测
      ├─ 基于文本静默时长
      ├─ 基于语音能量消失
      └─ → _deliver(text, reason)
          └─ on_user_utterance(text)
```

## 关键概念

### 端点检测

当前使用纯静默时间判定，不依赖 sherpa-onnx 的内置端点检测。

条件：
- 文本稳定未变 ≥ `silence_endpoint_delay` 秒
- 有 ≥ 2 个字符
- 距离上次交付 ≥ 1.5s（防重复交付冷却）
- 语音能量已消失 ≥ `silence_endpoint_delay` 秒

### 重叠分类

当 AI 正在说话（TTSState.STREAMING / DRAINING）时用户开口，用 LLM 分类：

```text
_check_overlap(user_text, ai_context)
  │
  ▼
overlap_classifier (小模型, qwen2.5:0.5b)
  │
  ├─ continue   → 用户在附和，AI 继续
  ├─ soft_break → 用户开始说实质性内容，AI 把当前句说完再让
  └─ hard_break → 用户紧急打断，AI 立刻停
```

Debounce：前 2 次 partial 更新不触发分类（等有足够上下文）。

### TTS 回声保护

cli.py 中通过 `recent_tts_texts` 队列（8s 窗口）过滤疑似被麦克风拾取的 TTS 回声：

```text
is_probable_tts_echo(text)
  → 与 recent_tts_texts 中的文本做归一化匹配
  → 命中则丢弃，不进入对话流
```

### 线程安全性

`feed_audio()` 使用 RLock，所以 `reset_stream()` 从回调内调用也不会死锁。
