# 语音识别 (STT)

## 架构

STT 子系统由三部分组成：

```
麦克风 → denoise() 去噪 → sherpa-onnx 流式 ASR → 原始文本片段
                                                    ↓
                                             ConversationPool
                                         (静默 1.0s 判定稳定)
                                                    ↓
                            ┌────────────────────────┼────────────────────────┐
                            ↓                        ↓                        ↓
                     separate 模式              inline 模式               none 模式
                  独立 LLM 精炼修正           local_clean_stt()         local_clean_stt()
                  (修正同音错字)           + 聊天 LLM 隐式纠错          (仅正则清洗)
                            ↓                        ↓                        ↓
                            完整句子 → on_refined() → ChatSession
```

## 流式识别：stt.py

基于 sherpa-onnx 的流式语音识别：

- **模型**：使用 Zipformer-Transducer 架构，`sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`，支持中英双语。首次运行自动下载到 `stt_model_dir`
- **运行方式**：在独立线程中持续读取麦克风输入（`sounddevice.InputStream`，16kHz 单声道 float32）
- **去噪**：`denoise()` 函数做三阶段处理：DC 偏移消除 → 一阶 IIR 高通滤波（~80Hz 截止）→ RMS 噪声门控（阈值 0.002）
- **端点检测**：sherpa-onnx 内置三条规则 — 尾部静音 3.0s（兜底）、尾部静音 2.0s、最长 15s 强制断句
- **设备选择**：`find_input_device()` 自动选择最佳麦克风，优先级 MME > DirectSound > WASAPI，排除虚拟音频设备
- **TTS 暂停**：`stt_pause_during_tts = true` 时，TTS 播放期间静音输入并重建识别流，防止扬声器信号被麦克风捕获

### 主要函数

| 函数 | 功能 |
|------|------|
| `denoise(audio, sample_rate=16000)` | 音频去噪（DC 消除 + 高通滤波 + 噪声门控），纯 numpy 实现 |
| `find_input_device()` | 自动选择最佳麦克风设备 |
| `list_devices()` | 打印所有音频设备信息 |
| `download_model(model_dir)` | 检查并下载 STT 模型，自动解压 tar.bz2 |
| `create_recognizer(model_path, args)` | 创建 sherpa-onnx OnlineRecognizer |

### 常量

- `SAMPLE_RATE = 16000`（sherpa-onnx 固定采样率）
- `MODEL_URL` 指向 GitHub Release 的 zipformer 双语模型

## 精炼池：pool.py

`ConversationPool` 解决流式 ASR 输出碎片化的问题：

1. **累积**：将 ASR 输出的短文本片段存入，去重（相同文本不重复添加；新文本比已有文本短时重置状态）
2. **静默判定**：`stt_refine_stable_seconds`（默认 1.0 秒）内无新片段到达 → 认为一句话结束
3. **精炼策略**（由 `stt_refine_mode` 决定）：

### separate 模式

- **短文本跳过**：`stt_skip_short_refine = true` 时，长度 ≤ `stt_skip_short_refine_max_chars`（18）且无口吃重复/过多标点的文本直接跳过 LLM 精炼
- **LLM 精炼**：调用 `stt_refine_model`（默认 `qwen2.5:1.5b`），使用 `stt_refine.system` 和 `stt_refine.user_template` 提示词进行纠错。兼容 OpenAI API 和 Ollama API 两种请求格式
- 精炼 LLM 独立于聊天 LLM，串行阻塞

### inline 模式

- 不调用独立精炼 LLM
- 仅执行 `local_clean_stt()` 本地正则清洗
- 在 `build_messages(stt_refine_inline=True)` 时注入 `stt_refine_inline.system` 提示词，让聊天 LLM 在回复时隐式纠正少量同音错字
- 延迟最低，适合 STT 准确率较高的场景

### none 模式

- 仅执行 `local_clean_stt()` 本地正则清洗
- 不调用任何 LLM 精炼，零 LLM 开销
- 适合 STT 准确率已经很高的场景

### local_clean_stt()

`pool.py` 中的纯本地正则清洗函数，零延迟：

- 字级别口吃："我我我想" → "我想"（相同汉字 3+ 次重复）
- 词级别口吃："那个那个那个" → "那个"（2-4 字短语 3+ 次重复）
- 过多标点："！！！" → "！"
- 空白字符规范化

### 精炼原则（separate 模式 LLM 精炼）

- 只修正明确的同音错字
- 去除口吃重复
- 不改变措辞、语序、句式
- 不删减内容，不添加原文没有的内容
- 无错误时保持原文不变

### 回调机制

`ConversationPool` 支持三个可选回调，供状态机追踪精炼过程：

| 回调 | 触发时机 |
|------|---------|
| `on_refine_start()` | 开始精炼（LLM 调用前） |
| `on_refine_done()` | 精炼完成（LLM 返回后） |
| `on_refined(text)` | 精炼结果就绪，送入对话流程 |

精炼后的完整句子通过 `on_refined(text)` 回调送入 ChatSession。

## 依赖

```bash
pip install sherpa-onnx sounddevice numpy
```
