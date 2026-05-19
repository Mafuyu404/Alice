# STT 语音识别

## 模块构成

| 文件 | 职责 |
|---|---|
| `kokoro/stt.py` | 模型下载、识别器创建、音频设备枚举、去噪 |
| `kokoro/conversation.py` | 流式输入处理、端点检测、重叠分类 |
| `kokoro/aec.py` | 声学回声消除 |

## 识别器

基于 sherpa-onnx 的 Zipformer-Transducer 流式识别器，中英双语。

### 模型

默认从 GitHub Releases 自动下载：`sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`

模型存储在 `models/stt/`，包含：
- `encoder-epoch-99-avg-1.onnx`
- `decoder-epoch-99-avg-1.onnx`
- `joiner-epoch-99-avg-1.onnx`
- `tokens.txt`

### 配置

```toml
stt_model_dir = "models/stt"
num_threads = 4        # 推理线程数
```

端点检测由 `ConversationManager` 在 Python 层控制，sherpa-onnx 内置的端点检测关闭。

## 音频处理链

```text
麦克风 → AEC 回声消除 → denoise 去噪 → sherpa-onnx 识别
```

### Denoise（纯 numpy）

三阶段：
1. DC 偏移消除（去均值）
2. 一阶 IIR 高通滤波，-3dB @ ~80Hz（滤除低频隆隆声）
3. RMS 噪声门控，阈值 0.0005（仅拦纯静音）

### AEC

WebRTC 音频处理模块实现。TTS 播放的参考信号与麦克风采集做相关性抵消。

配置参数：
- `delay_ms`：扬声器到麦克风的预估延迟（典型 30-80ms）
- `ns_level`：噪声抑制等级 0-4

### 回声过滤（文本层）

cli.py 中额外有一层文本级回声过滤：维护最近 8 秒内 TTS 输出的归一化文本队列，识别结果与队列命中的判定为回声并丢弃。

## 精炼模式

STT 原始输出是碎片化短文本，精炼选择：

| 模式 | 做法 | 延迟 | 质量 |
|---|---|---|---|
| `separate` | 独立 LLM 调用精炼 | 高 | 最高 |
| `inline` | 聊天 LLM 回复时隐式纠错 | 低 | 中 |
| `none` | 仅本地正则清洗 | 零 | 取决于 STT 质量 |

## 设备选择

自动扫描音频设备，优先级：
1. 排除虚拟设备（Cable、VB-Audio 等）
2. 按 API 类型优先：MME > DirectSound > WASAPI > WDM-KS
3. 逐个测试打开

可通过 `--device` 参数指定设备 ID。
