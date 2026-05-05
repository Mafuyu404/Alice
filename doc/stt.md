# 语音识别 (STT)

## 架构

STT 子系统由两部分组成：

```
麦克风 → sherpa-onnx 流式 ASR → 原始文本片段
                                      ↓
                               ConversationPool
                              (静默 0.6s 判定)
                                      ↓
                         LLM 精炼修正 / 直接跳过
                                      ↓
                              完整句子 → ChatSession
```

## 流式识别：stt.py

基于 sherpa-onnx 的流式语音识别：

- **模型**：自动下载 `sherpa-onnx-sense-voice-zh-en`，支持中英双语
- **运行方式**：在独立线程中持续读取麦克风输入
- **噪声处理**：忽略过短的音频片段，提高识别准确率
- **设备枚举**：`python cli.py --list-devices` 列出可用麦克风设备
- **TTS 暂停**：`stt_pause_during_tts = true` 时，TTS 播放期间静音输入，防止扬声器信号被麦克风捕获

## 精炼池：pool.py

`ConversationPool` 解决流式 ASR 输出碎片化的问题：

1. **累积**：将 ASR 输出的短文本片段存入队列，去重
2. **静默判定**：`stt_refine_stable_seconds`（默认 0.6 秒）内无新片段到达 → 认为一句话结束
3. **短文本跳过**：`stt_skip_short_refine = true` 时，长度 ≤ 18 字符且无明显 ASR 异常的文本直接跳过 LLM 精炼，降低延迟
4. **精炼**：长文本或有异常的文本调用小模型修正错别字，保持原意措辞不变
5. **回传**：精炼后的完整句子通过 `on_refined()` 回调送入 ChatSession

### 精炼原则

- 只修正明确的同音错字
- 去除口吃重复（"我我我想去" → "我想去"）
- 不改变措辞、语序、句式
- 不删减内容，不添加原文没有的内容
- 无错误时保持原文不变

## 依赖

```bash
pip install sherpa-onnx sounddevice numpy
```
