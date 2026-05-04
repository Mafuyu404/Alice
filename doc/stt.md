# 语音识别 (STT)

## 架构

STT 子系统由两部分组成：

```
麦克风 → sherpa-onnx 流式 ASR → 原始文本片段
                                      ↓
                               ConversationPool
                              (0.8s 静默检测)
                                      ↓
                               LLM 精炼修正
                                      ↓
                              完整句子 → ChatSession
```

## 流式识别：stt.py

基于 sherpa-onnx 的流式语音识别：

- **模型**：自动下载 Paraformer 小模型（`sherpa-onnx-sense-voice-zh-en`），支持中英双语
- **运行方式**：在独立线程中持续读取麦克风输入
- **噪声处理**：忽略过短的片段，提高识别准确率
- **设备枚举**：`python cli.py --list-devices` 列出可用麦克风
- **TTS 暂停**：`stt_pause_during_tts = true` 时，TTS 播放期间暂停麦克风采集，防止回声

## 精炼池：pool.py

`ConversationPool` 解决流式 ASR 输出碎片化的问题：

1. **累积**：将 ASR 输出的短文本片段按时间顺序存入队列
2. **触发**：检测到 0.8 秒静默（无新片段到达）时，触发精炼
3. **精炼**：调用小模型修正错别字，保持原意
   - 提示词见 `prompts.json` 的 `stt_refine` 部分
   - 默认行为：修正同音错字，保持措辞语序不变
4. **回传**：精炼后的完整句子送入 ChatSession

### 精炼原则

- 只修正明确的同音错字
- 不改变措辞、语序、句式
- 无错误时保持原文不变

## 依赖

```
pip install sherpa-onnx sounddevice numpy
```
