"""
kokoro/stt.py — 语音识别模块 (sherpa-onnx)

基于 sherpa-onnx 的本地流式语音识别。
支持流式解码、断句检测、自动模型下载。

用法:
    path = stt.download_model("models/stt")
    rec  = stt.create_recognizer(path, args)
    dev  = stt.find_input_device()
"""

import os
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import sherpa_onnx
import sounddevice as sd

# ── 常量 ──────────────────────────────────────────────────

SAMPLE_RATE = 16000  # sherpa-onnx 固定 16kHz

MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2"
)
MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"

REQUIRED_MODEL_FILES = [
    "tokens.txt",
    "encoder-epoch-99-avg-1.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.onnx",
]


# ── 音频去噪 ──────────────────────────────────────────────

def denoise(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """基础去噪音：DC 偏移消除 + 高通滤波 + 噪声门控。

    三阶段处理:
      1. DC 偏移消除（减去均值）
      2. 一阶 IIR 高通滤波器（-3dB 截止 ~80Hz），滤除低频隆隆声
      3. RMS 噪声门控：低于阈值的帧直接静音

    无额外依赖，纯 numpy 实现。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])
        sample_rate: 采样率，默认 16000

    Returns:
        处理后的音频数组
    """
    # 1. DC 偏移消除
    audio = audio - np.mean(audio)

    # 2. 一阶 IIR 高通滤波器（截止频率 ~80Hz）
    rc = 1.0 / (2.0 * np.pi * 80.0)
    dt = 1.0 / sample_rate
    alpha = rc / (rc + dt)
    y = np.zeros_like(audio)
    for i in range(1, len(audio)):
        y[i] = alpha * y[i - 1] + alpha * (audio[i] - audio[i - 1])

    # 3. RMS 噪声门控（阈值设得很低，只拦完全静音，不拦轻声说话）
    rms = np.sqrt(np.mean(y ** 2))
    if rms < 0.0005:
        return np.zeros_like(audio)

    return y


# ── 音频设备 ──────────────────────────────────────────────

def find_input_device() -> int | None:
    """自动选择合适的麦克风设备。

    优先级:
      1. 排除虚拟设备（Cable、VB-Audio 等）
      2. 按 API 类型优先: MME > DirectSound > WASAPI > WDM-KS
      3. 逐个测试打开，第一个可用的即为选中

    Returns:
        设备 ID，未找到则返回 None
    """
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    api_priority = {"mme": 0, "directsound": 1, "wasapi": 2, "wdm-ks": 3}
    skip_keywords = ["cable", "vb-audio", "v-b audio", "point"]

    candidates = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] == 0:
            continue
        name_lower = dev["name"].lower()
        if any(kw in name_lower for kw in skip_keywords):
            continue
        # 只考虑带 () 的"真正"设备名，过滤虚拟聚合设备
        if "(" not in dev["name"] and ")" not in dev["name"]:
            continue
        ha_name = hostapis[dev["hostapi"]]["name"].lower()
        prio = next((p for k, p in api_priority.items() if k in ha_name), 99)
        candidates.append((prio, i, dev))

    candidates.sort(key=lambda x: x[0])

    for prio, i, dev in candidates:
        try:
            sd.InputStream(
                device=i, channels=1, samplerate=16000, dtype="float32"
            ).close()
            print(f"  [stt] 选择麦克风: [{i}] {dev['name']}")
            return i
        except Exception:
            continue

    return None


def list_devices():
    """打印所有音频设备信息。"""
    print(sd.query_devices())


# ── 模型管理 ──────────────────────────────────────────────

def download_model(model_dir: str) -> str:
    """检查并下载 STT 模型。

    如果模型已存在且文件完整则跳过下载。
    下载后自动解压 tar.bz2 并清理压缩包。

    Args:
        model_dir: 模型存放目录

    Returns:
        模型路径（字符串）
    """
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / MODEL_NAME

    # 检查已下载的模型文件是否完整
    if model_path.exists():
        missing = [f for f in REQUIRED_MODEL_FILES if not (model_path / f).exists()]
        if not missing:
            print(f"  [stt] 模型已存在: {model_path}")
            return str(model_path)

    # 下载
    archive_name = os.path.basename(MODEL_URL)
    archive_path = model_dir / archive_name

    print(f"  [stt] 正在下载模型: {MODEL_NAME}")

    def reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size / 1024
        if total_size > 0:
            total = total_size / 1024
            percent = min(downloaded / total * 100, 100)
            bar_len = 40
            filled = int(bar_len * downloaded / total)
            bar = "=" * filled + "-" * (bar_len - filled)
            sys.stdout.write(f"\r  [{bar}] {percent:.0f}% ({downloaded:.0f}/{total:.0f} KB")
        sys.stdout.flush()

    urllib.request.urlretrieve(MODEL_URL, archive_path, reporthook)
    print("\n  [stt] 下载完成，正在解压...")

    with tarfile.open(archive_path, "r:bz2") as tar:
        tar.extractall(path=model_dir)

    archive_path.unlink()
    print(f"  [stt] 模型已解压到: {model_path}")
    return str(model_path)


# ── 识别器 ────────────────────────────────────────────────

def create_recognizer(model_path: str, args) -> sherpa_onnx.OnlineRecognizer:
    """创建 sherpa-onnx 流式识别器。

    使用 Zipformer-Transducer 架构，启用了端点检测。
    默认 4 线程推理，支持热词功能。

    Args:
        model_path: 模型所在目录路径
        args: 参数对象（需含 num_threads, hotwords, hotwords_score, verbose）

    Returns:
        OnlineRecognizer 实例
    """
    print(f"  [stt] 加载模型: {os.path.basename(model_path)}")

    return sherpa_onnx.online_recognizer.OnlineRecognizer.from_transducer(
        tokens=os.path.join(model_path, "tokens.txt"),
        encoder=os.path.join(model_path, "encoder-epoch-99-avg-1.onnx"),
        decoder=os.path.join(model_path, "decoder-epoch-99-avg-1.onnx"),
        joiner=os.path.join(model_path, "joiner-epoch-99-avg-1.onnx"),
        num_threads=getattr(args, "num_threads", 4),
        enable_endpoint_detection=False,  # 由 ConversationManager 自己控制断句
        rule1_min_trailing_silence=3.0,
        rule2_min_trailing_silence=0,
        rule3_min_utterance_length=20,
        decoding_method="greedy_search",
        hotwords_file=getattr(args, "hotwords", ""),
        hotwords_score=getattr(args, "hotwords_score", 1.5),
        debug=getattr(args, "verbose", False),
    )
