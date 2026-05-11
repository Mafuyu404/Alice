"""Alice 配置可视化编辑器 — 桌面应用

参考 overlay_slideshow.py 的 PySide6 实现风格，
提供图形化界面编辑 config.toml / config.json / characters.json / prompts.json。

用法:
    python config_editor.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# ── paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
CONFIG_TOML = ROOT / "config.toml"
CONFIG_JSON = ROOT / "config.json"
CHARACTERS_JSON = ROOT / "characters.json"
PROMPTS_JSON = ROOT / "prompts.json"
CHARACTERS_DIR = ROOT / "characters"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — File I/O
# ══════════════════════════════════════════════════════════════════════════════

def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_all_characters() -> dict[str, dict]:
    """Load per-character JSON files from characters/*/ dirs."""
    chars: dict[str, dict] = {}
    if not CHARACTERS_DIR.is_dir():
        return chars
    for entry in sorted(CHARACTERS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        char_file = entry / f"{entry.name}.json"
        if not char_file.exists():
            continue
        try:
            data = json.loads(char_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name"):
                chars[entry.name] = data
        except Exception:
            continue
    return chars


def save_character(character_id: str, data: dict) -> None:
    char_file = CHARACTERS_DIR / character_id / f"{character_id}.json"
    char_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_per_character_toml(character_id: str) -> dict:
    path = CHARACTERS_DIR / character_id / "config.toml"
    return load_toml(path) if path.exists() else {}


def save_per_character_toml(character_id: str, data: dict) -> None:
    path = CHARACTERS_DIR / character_id / "config.toml"
    lines = ["# 角色专用 LLM 配置覆盖\n"]
    for k, v in data.items():
        if isinstance(v, str):
            lines.append(f'{k} = "{v}"\n')
        elif isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}\n")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}\n")
        elif isinstance(v, float):
            lines.append(f"{k} = {v}\n")
    path.write_text("".join(lines), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TOML config reader / writer
# ══════════════════════════════════════════════════════════════════════════════

def _toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, list):
        items = ", ".join(_toml_val(i) for i in v)
        return f"[{items}]"
    # string — escape quotes and backslashes
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _comment(text: str) -> str:
    """Return a TOML comment block (strip blank lines)."""
    if not text:
        return ""
    lines = []
    for line in text.strip().split("\n"):
        lines.append(f"# {line}")
    return "\n".join(lines) + "\n"


# ── TOML section templates — each is a function that takes the config dict
#    and returns TOML string for that section ────────────────────────────────

SECTIONS = [
    "llm", "tts", "stt", "aec", "memory", "portrait_overlay",
    "subtitle", "subtitle_stt", "vision", "edge_page_cache",
    "screen_watch", "impulse", "bilibili_live", "mem0_llm",
    "mem0_embedder", "mem0_lifecycle", "tool_calling",
]


def toml_llm(cfg: dict) -> str:
    c = cfg.get("llm", cfg)
    lines = [
        _comment("LLM 路由"),
        _comment("LLM 服务地址（兼容 OpenAI / Ollama 格式）。"),
        _comment('Ollama 默认: http://127.0.0.1:11434'),
        f"llm_url = {_toml_val(c.get('llm_url', 'http://127.0.0.1:11434'))}\n",
        _comment("默认对话模型名称。"),
        _comment('Ollama: "qwen2.5:7b", "deepseek-v4-flash" 等'),
        _comment('模型名以 "deepseek" 开头时自动走 DeepSeek API'),
        f"llm_model = {_toml_val(c.get('llm_model', 'deepseek-v4-flash'))}\n",
        _comment("DeepSeek API 密钥。留空时从 config.json 读取。"),
        _comment("也可通过环境变量 DEEPSEEK_API_KEY 设置（优先级更高）。"),
        f"deepseek_api_key = {_toml_val(c.get('deepseek_api_key', ''))}\n",
        _comment("智谱 CharGLM API 密钥。使用 charglm 模型时需要。"),
        f"charglm_api_key = {_toml_val(c.get('charglm_api_key', ''))}\n",
        _comment("本地 transformers 模型名（LLM 不可用时的后备）。"),
        f"local_model = {_toml_val(c.get('local_model', 'Qwen/Qwen2.5-1.5B-Instruct'))}\n",
    ]
    return "\n".join(lines)


def toml_tts(cfg: dict) -> str:
    c = cfg.get("tts", cfg)
    lines = [
        _comment("TTS — 文本转语音"),
        _comment('TTS 后端。可选: "minimax", "cartesia"'),
        f"tts_backend = {_toml_val(c.get('tts_backend', 'minimax'))}\n",
        _comment("TTS 播放音量倍率 (0.0 ~ 2.0)。1.0 = 原始音量"),
        f"tts_volume = {_toml_val(c.get('tts_volume', 1.0))}\n",
        _comment("── Cartesia 配置 ──"),
        f"cartesia_api_key = {_toml_val(c.get('cartesia_api_key', ''))}\n",
        f"tts_voice_id = {_toml_val(c.get('tts_voice_id', ''))}\n",
        _comment("Cartesia 采样率 (Hz)。可选: 8000/16000/22050/24000/44100/48000"),
        f"tts_sample_rate = {_toml_val(c.get('tts_sample_rate', 24000))}\n",
        _comment("── MiniMax 配置 ──"),
        f"minimax_api_key = {_toml_val(c.get('minimax_api_key', ''))}\n",
        _comment('MiniMax 模型。可选: "speech-2.8-turbo"（推荐）, "speech-2.8-pro"'),
        f"minimax_model = {_toml_val(c.get('minimax_model', 'speech-2.8-turbo'))}\n",
        _comment("MiniMax 采样率 (Hz)"),
        f"minimax_sample_rate = {_toml_val(c.get('minimax_sample_rate', 32000))}\n",
        _comment("语速倍率 (0.5 ~ 2.0)。推荐 0.9 ~ 1.2"),
        f"minimax_tts_speed = {_toml_val(c.get('minimax_tts_speed', 1.1))}\n",
        _comment("播放前预缓冲秒数。流畅优先设 0.3 ~ 0.5，敏感场景设 0.05 ~ 0.15"),
        f"minimax_tts_buffer_seconds = {_toml_val(c.get('minimax_tts_buffer_seconds', 0.3))}\n",
        _comment("LLM 流式累积多少字符后强制刷新 TTS 缓冲区。推荐 20 ~ 40"),
        f"tts_stream_chunk_chars = {_toml_val(c.get('tts_stream_chunk_chars', 28))}\n",
        _comment("触发句子刷新的最小字符数。推荐 6 ~ 12"),
        f"tts_stream_sentence_min_chars = {_toml_val(c.get('tts_stream_sentence_min_chars', 8))}\n",
    ]
    return "\n".join(lines)


def toml_aec(cfg: dict) -> str:
    c = cfg.get("aec", {})
    lines = [
        _comment("AEC — 声学回声消除"),
        _comment("通过 WebRTC AEC 消除 TTS 外放被麦克风拾取的回声。"),
        f"\n[aec]\n",
        _comment("启用回声消除"),
        f"enabled = {_toml_val(c.get('enabled', False))}\n",
        _comment("麦克风到扬声器的预估延迟 (ms)。建议从 50 开始调试。"),
        f"delay_ms = {_toml_val(c.get('delay_ms', 50))}\n",
        _comment("噪声抑制等级 (0 ~ 4)。2 = 中等推荐"),
        f"ns_level = {_toml_val(c.get('ns_level', 2))}\n",
    ]
    return "".join(lines)


def toml_stt(cfg: dict) -> str:
    c = cfg.get("stt", cfg)
    lines = [
        _comment("STT — 语音转文字"),
        _comment("流式 ASR 模型存放目录"),
        f"stt_model_dir = {_toml_val(c.get('stt_model_dir', 'models/stt'))}\n",
        _comment("STT 精炼用小模型。建议 1.5b ~ 7b。设为 '' 跳过精炼"),
        f"stt_refine_model = {_toml_val(c.get('stt_refine_model', 'qwen2.5:1.5b'))}\n",
        _comment("认知层评估用模型。留空则使用 llm_model"),
        f"cognition_model = {_toml_val(c.get('cognition_model', ''))}\n",
        _comment("认知层评估频率（每 N 轮）。0 = 禁用"),
        f"cognition_eval_interval = {_toml_val(c.get('cognition_eval_interval', 5))}\n",
        _comment("情绪层评估用模型。留空则使用 llm_model"),
        f"emotion_model = {_toml_val(c.get('emotion_model', ''))}\n",
        _comment('STT 精炼模式: "separate", "inline", "none"'),
        f"stt_refine_mode = {_toml_val(c.get('stt_refine_mode', 'inline'))}\n",
        _comment("STT 文本稳定判定间隔 (秒)。推荐 1.0 ~ 2.0"),
        f"stt_refine_stable_seconds = {_toml_val(c.get('stt_refine_stable_seconds', 0.7))}\n",
        _comment("对话池轮询间隔 (秒)。建议保持 0.05"),
        f"stt_pool_tick_seconds = {_toml_val(c.get('stt_pool_tick_seconds', 0.05))}\n",
        _comment("STT 精炼最大 token 数。64 ~ 128 足够"),
        f"stt_refine_max_tokens = {_toml_val(c.get('stt_refine_max_tokens', 128))}\n",
        _comment("跳过短文本精炼"),
        f"stt_skip_short_refine = {_toml_val(c.get('stt_skip_short_refine', True))}\n",
        _comment("跳过精炼的最大字符数"),
        f"stt_skip_short_refine_max_chars = {_toml_val(c.get('stt_skip_short_refine_max_chars', 18))}\n",
        _comment("TTS 播放期间暂停麦克风输入"),
        f"stt_pause_during_tts = {_toml_val(c.get('stt_pause_during_tts', True))}\n",
    ]
    return "\n".join(lines)


def toml_memory(cfg: dict) -> str:
    c = cfg.get("memory", cfg)
    lines = [
        _comment("记忆后端"),
        _comment('可选: "none", "mem0", "kokoromemo"'),
        f"memory_backend = {_toml_val(c.get('memory_backend', 'mem0'))}\n",
        _comment("KokoroMemo 外部记忆服务"),
        f"kokoromo_url = {_toml_val(c.get('kokoromo_url', 'http://127.0.0.1:14514'))}\n",
        f"kokoromo_dir = {_toml_val(c.get('kokoromo_dir', 'D:/program/kokoromemo'))}\n",
    ]
    return "\n".join(lines)


def toml_portrait(cfg: dict) -> str:
    c = cfg.get("portrait_overlay", cfg)
    lines = [
        _comment("立绘覆盖层 — 透明立绘窗口 (PySide6)"),
        f"portrait_overlay_host = {_toml_val(c.get('portrait_overlay_host', '127.0.0.1'))}\n",
        f"portrait_overlay_port = {_toml_val(c.get('portrait_overlay_port', 17352))}\n",
        _comment("立绘决策循环间隔 (秒)。0 = 后端默认 (~2 秒)"),
        f"portrait_decision_interval = {_toml_val(c.get('portrait_decision_interval', 0.0))}\n",
        _comment("对话结束后多少秒恢复平静表情。推荐 30 ~ 120"),
        f"portrait_decay_seconds = {_toml_val(c.get('portrait_decay_seconds', 60.0))}\n",
        _comment("立绘窗口显示冲动值调试信息"),
        f"portrait_debug_overlay = {_toml_val(c.get('portrait_debug_overlay', False))}\n",
        _comment("鼠标点击穿透立绘窗口"),
        f"portrait_click_through = {_toml_val(c.get('portrait_click_through', False))}\n",
        _comment("立绘表情选择模型。留空则使用对话 LLM"),
        f"portrait_model = {_toml_val(c.get('portrait_model', 'qwen2.5:1.5b'))}\n",
    ]
    return "\n".join(lines)


def toml_subtitle(cfg: dict) -> str:
    c = cfg.get("subtitle", {})
    lines = [
        _comment("字幕覆盖层 — 流式显示 LLM 输出"),
        _comment("由 overlay_subtitle.py 实现。透明背景，白描深红字体。"),
        _comment("开关与立绘绑定。"),
        f"\n[subtitle]\n",
        _comment("字体颜色 (CSS 颜色值)"),
        f"font_color = {_toml_val(c.get('font_color', '#8b0000'))}\n",
        _comment("字体描边颜色"),
        f"stroke_color = {_toml_val(c.get('stroke_color', '#ffffff'))}\n",
        _comment("字号 (px)。推荐 18 ~ 48"),
        f"font_size = {_toml_val(c.get('font_size', 30))}\n",
        f"subtitle_host = {_toml_val(c.get('subtitle_host', '127.0.0.1'))}\n",
        f"subtitle_port = {_toml_val(c.get('subtitle_port', 17353))}\n",
    ]
    return "".join(lines)


def toml_subtitle_stt(cfg: dict) -> str:
    c = cfg.get("subtitle_stt", {})
    lines = [
        _comment("STT 字幕单独配置（独立端口，深蓝色描边）"),
        f"\n[subtitle_stt]\n",
        f"font_color = {_toml_val(c.get('font_color', '#00008b'))}\n",
        f"stroke_color = {_toml_val(c.get('stroke_color', '#ffffff'))}\n",
        f"font_size = {_toml_val(c.get('font_size', 30))}\n",
        f"btn_color = {_toml_val(c.get('btn_color', '#4444ff'))}\n",
        f"subtitle_host = {_toml_val(c.get('subtitle_host', '127.0.0.1'))}\n",
        f"subtitle_port = {_toml_val(c.get('subtitle_port', 17354))}\n",
    ]
    return "".join(lines)


def toml_vision(cfg: dict) -> str:
    c = cfg.get("vision", cfg)
    lines = [
        _comment("视觉 / 屏幕识别"),
        _comment('后端: "dashscope"（推荐）, "ollama"'),
        f"vision_backend = {_toml_val(c.get('vision_backend', 'dashscope'))}\n",
        _comment('模型: dashscope: "qwen-vl-plus", ollama: "llava" 等'),
        f"vision_model = {_toml_val(c.get('vision_model', 'qwen-vl-plus'))}\n",
        _comment("DashScope API 密钥。也可通过环境变量设置。"),
        f"vision_api_key = {_toml_val(c.get('vision_api_key', ''))}\n",
        _comment("截图缩放上限 (像素总数)。0 = 禁用缩放。默认 921600 = 1280×720"),
        f"vision_max_pixels = {_toml_val(c.get('vision_max_pixels', 921600))}\n",
    ]
    return "\n".join(lines)


def toml_edge_cache(cfg: dict) -> str:
    c = cfg.get("edge_page_cache", {})
    lines = [
        _comment("Edge 网页缓存 — 周期性读取当前 Edge 标签页正文"),
        _comment("需要以远程调试端口启动 Edge。"),
        f"\n[edge_page_cache]\n",
        f"enabled = {_toml_val(c.get('enabled', True))}\n",
        _comment("抓取间隔 (秒)"),
        f"interval_seconds = {_toml_val(c.get('interval_seconds', 3.0))}\n",
        f"devtools_host = {_toml_val(c.get('devtools_host', '127.0.0.1'))}\n",
        f"devtools_port = {_toml_val(c.get('devtools_port', 9222))}\n",
        f"cache_file = {_toml_val(c.get('cache_file', 'data/edge_page_cache.json'))}\n",
        _comment("最大保存字符数"),
        f"max_chars = {_toml_val(c.get('max_chars', 12000))}\n",
        _comment("DevTools 请求超时 (秒)"),
        f"request_timeout = {_toml_val(c.get('request_timeout', 3.0))}\n",
    ]
    return "".join(lines)


def toml_screen_watch(cfg: dict) -> str:
    c = cfg.get("screen_watch", {})
    lines = [
        _comment("屏幕监控 — 周期性截图 + 视觉分析"),
        f"\n[screen_watch]\n",
        f"enabled = {_toml_val(c.get('enabled', False))}\n",
        _comment("截图分析最小间隔 (秒)。推荐 30 ~ 120"),
        f"watch_interval = {_toml_val(c.get('watch_interval', 3.0))}\n",
        _comment("兴趣度阈值 (0-100)。推荐 50 ~ 80"),
        f"interest_threshold = {_toml_val(c.get('interest_threshold', 70.0))}\n",
        _comment("单次视觉分析超时 (秒)"),
        f"vision_timeout = {_toml_val(c.get('vision_timeout', 45))}\n",
    ]
    return "".join(lines)


def toml_impulse(cfg: dict) -> str:
    c = cfg.get("impulse", {})
    lines = [
        _comment("主动语音调度器 v2 — LLM 规划式"),
        _comment("基于 LLM 规划的主动搭话机制。"),
        f"\n[impulse]\n",
        f"enabled = {_toml_val(c.get('enabled', True))}\n",
        _comment("计划表最大容量。推荐 3 ~ 8"),
        f"max_plans = {_toml_val(c.get('max_plans', 5))}\n",
        _comment("计划表最小容量。推荐 0 ~ 3"),
        f"min_plans = {_toml_val(c.get('min_plans', 1))}\n",
        _comment("规划用模型名。留空则使用默认对话模型"),
        f"planning_model = {_toml_val(c.get('planning_model', 'deepseek-v4-flash'))}\n",
        _comment("规划时屏幕分析超时 (秒)。推荐 30 ~ 60"),
        f"screen_timeout = {_toml_val(c.get('screen_timeout', 45))}\n",
        _comment("计划表为空时重试等待秒数。推荐 30 ~ 120"),
        f"empty_plan_retry_seconds = {_toml_val(c.get('empty_plan_retry_seconds', 10.0))}\n",
        _comment("控制台输出计划表内容（调试）"),
        f"log_plan_table = {_toml_val(c.get('log_plan_table', True))}\n",
        _comment("同一记忆事件冷却时间 (秒)。默认 6 小时"),
        f"memory_cooldown_seconds = {_toml_val(c.get('memory_cooldown_seconds', 21600.0))}\n",
        _comment("日期匹配记忆事件注入的基础冲动值"),
        f"memory_date_score = {_toml_val(c.get('memory_date_score', 50.0))}\n",
        _comment("记忆查询结果注入的基础冲动值"),
        f"memory_lookup_score = {_toml_val(c.get('memory_lookup_score', 70.0))}\n",
        _comment("记忆查询 LLM 提示语"),
        f"memory_lookup_query = {_toml_val(c.get('memory_lookup_query', 'recent important user preferences, plans, dates, anniversaries, goals'))}\n",
        _comment("开启周期性记忆事件轮询"),
        f"memory_events_enabled = {_toml_val(c.get('memory_events_enabled', True))}\n",
        _comment("记忆事件轮询间隔 (秒)。推荐 120 ~ 600"),
        f"memory_check_interval = {_toml_val(c.get('memory_check_interval', 300.0))}\n",
    ]
    return "".join(lines)


def toml_bilibili(cfg: dict) -> str:
    c = cfg.get("bilibili_live", {})
    lines = [
        _comment("Bilibili 直播间弹幕"),
        f"\n[bilibili_live]\n",
        f"enabled = {_toml_val(c.get('enabled', True))}\n",
        _comment("直播模式。启用后 AI 会选择性回复弹幕。"),
        f"live_mode = {_toml_val(c.get('live_mode', True))}\n",
        _comment("Bilibili 直播间房间号"),
        f"room_id = {_toml_val(c.get('room_id', 1796292397))}\n",
        _comment("弹幕最长保留时间 (秒)。推荐 60 ~ 300"),
        f"buffer_max_age = {_toml_val(c.get('buffer_max_age', 60.0))}\n",
        _comment("WebSocket 重连等待时间 (秒)"),
        f"reconnect_delay = {_toml_val(c.get('reconnect_delay', 5.0))}\n",
    ]
    return "".join(lines)


def toml_mem0_llm(cfg: dict) -> str:
    c = cfg.get("mem0", {}).get("llm", {})
    lines = [
        _comment("mem0 — 内部记忆服务 LLM 配置"),
        f"\n[mem0.llm]\n",
        f"provider = {_toml_val(c.get('provider', 'ollama'))}\n",
        f"base_url = {_toml_val(c.get('base_url', 'http://127.0.0.1:11434'))}\n",
        _comment("mem0 内部用小模型。1.5b 足够"),
        f"model = {_toml_val(c.get('model', 'qwen2.5:1.5b'))}\n",
    ]
    return "".join(lines)


def toml_mem0_embedder(cfg: dict) -> str:
    c = cfg.get("mem0", {}).get("embedder", {})
    lines = [
        _comment("mem0 向量搜索嵌入模型"),
        f"\n[mem0.embedder]\n",
        f"provider = {_toml_val(c.get('provider', 'fastembed'))}\n",
        f"model = {_toml_val(c.get('model', 'BAAI/bge-small-zh-v1.5'))}\n",
        _comment("嵌入向量维度 (bge-small → 512, bge-base → 768)"),
        f"embedding_dims = {_toml_val(c.get('embedding_dims', 512))}\n",
    ]
    return "".join(lines)


def toml_mem0_lifecycle(cfg: dict) -> str:
    c = cfg.get("mem0", {}).get("lifecycle", {})
    lines = [
        _comment("mem0 记忆生命周期"),
        f"\n[mem0.lifecycle]\n",
        _comment('重要度模式: "auto", "always"'),
        f"importance_mode = {_toml_val(c.get('importance_mode', 'auto'))}\n",
        _comment("召回最小相似度阈值 (0.0 ~ 1.0)。推荐 0.2 ~ 0.5"),
        f"search_threshold = {_toml_val(c.get('search_threshold', 0.3))}\n",
        _comment("每次召回最大条数。推荐 3 ~ 12"),
        f"search_top_k = {_toml_val(c.get('search_top_k', 8))}\n",
    ]
    return "".join(lines)


def toml_tool_calling(cfg: dict) -> str:
    c = cfg.get("tool_calling", {})
    lines = [
        _comment("工具调用 (Tool Calling / Function Calling)"),
        f"\n[tool_calling]\n",
        f"enabled = {_toml_val(c.get('enabled', True))}\n",
        _comment("启用的工具列表"),
        _comment('可选: "look_at_screen", "search_memory", "get_current_time", "get_current_app", "save_to_memory"'),
    ]
    tools = c.get("tools", ["look_at_screen", "search_memory", "get_current_time", "get_current_app", "save_to_memory"])
    lines.append(f"tools = {_toml_val(tools)}\n")
    lines.append(_comment("单次对话中工具调用最大循环次数。建议 3 ~ 8"))
    lines.append(f"max_iterations = {_toml_val(c.get('max_iterations', 5))}\n")
    lines.append(_comment("单次工具调用超时 (秒)"))
    lines.append(f"tool_timeout = {_toml_val(c.get('tool_timeout', 45.0))}\n")
    return "".join(lines)


def write_config_toml(path: Path, cfg: dict) -> None:
    """Write config.toml from the merged config dict.

    ⚠ TOML rule: all top-level (non-section) keys must come before any
    [section] headers.  Flat-key functions first, section functions last.
    """
    parts = [
        _comment("Alice Chat 框架配置文件"),
        _comment("真实的 API 密钥不要提交到 git。空密钥会禁用对应的云功能。"),
        _comment("所有可配置项均已列出。"),
        "",
        # ── Flat (top-level) keys ──
        toml_llm(cfg),
        "",
        toml_tts(cfg),
        "",
        toml_stt(cfg),
        "",
        toml_memory(cfg),
        "",
        toml_portrait(cfg),
        "",
        toml_vision(cfg),
        "",
        # ── Section keys ──
        toml_aec(cfg),
        toml_subtitle(cfg),
        toml_subtitle_stt(cfg),
        toml_edge_cache(cfg),
        toml_screen_watch(cfg),
        toml_impulse(cfg),
        toml_bilibili(cfg),
        toml_mem0_llm(cfg),
        toml_mem0_embedder(cfg),
        toml_mem0_lifecycle(cfg),
        toml_tool_calling(cfg),
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Form field helpers
# ══════════════════════════════════════════════════════════════════════════════

class FieldRow:
    """Binds a widget to a config key for read/write."""

    def __init__(self, key: str, widget: QWidget, section: str | None = None):
        self.key = key
        self.widget = widget
        self.section = section

    def get_value(self):
        raise NotImplementedError

    def set_value(self, v):
        raise NotImplementedError


class StringField(FieldRow):
    def __init__(self, key: str, widget: QLineEdit, section: str | None = None):
        super().__init__(key, widget, section)
        self.widget = widget

    def get_value(self):
        return self.widget.text()

    def set_value(self, v):
        self.widget.setText(str(v) if v is not None else "")


class SecretField(FieldRow):
    def __init__(self, key: str, widget: QLineEdit, section: str | None = None):
        super().__init__(key, widget, section)
        self.widget = widget
        self.widget.setEchoMode(QLineEdit.EchoMode.Password)
        self.widget.setPlaceholderText("留空则禁用此项云服务")

    def get_value(self):
        return self.widget.text()

    def set_value(self, v):
        self.widget.setText(str(v) if v is not None else "")


class IntField(FieldRow):
    def __init__(self, key: str, widget: QSpinBox, section: str | None = None,
                 mini: int = 0, maxi: int = 999999):
        super().__init__(key, widget, section)
        self.widget = widget
        self.widget.setRange(mini, maxi)

    def get_value(self):
        return self.widget.value()

    def set_value(self, v):
        self.widget.setValue(int(v) if v is not None else 0)


class FloatField(FieldRow):
    def __init__(self, key: str, widget: QDoubleSpinBox, section: str | None = None,
                 mini: float = 0.0, maxi: float = 999999.0, step: float = 0.1,
                 decimals: int = 2):
        super().__init__(key, widget, section)
        self.widget = widget
        self.widget.setRange(mini, maxi)
        self.widget.setSingleStep(step)
        self.widget.setDecimals(decimals)

    def get_value(self):
        return self.widget.value()

    def set_value(self, v):
        self.widget.setValue(float(v) if v is not None else 0.0)


class BoolField(FieldRow):
    def __init__(self, key: str, widget: QCheckBox, section: str | None = None):
        super().__init__(key, widget, section)
        self.widget = widget

    def get_value(self):
        return self.widget.isChecked()

    def set_value(self, v):
        self.widget.setChecked(bool(v) if v is not None else False)


class ChoiceField(FieldRow):
    def __init__(self, key: str, widget: QComboBox, choices: list[str],
                 section: str | None = None):
        super().__init__(key, widget, section)
        self.widget = widget
        self.widget.addItems(choices)

    def get_value(self):
        return self.widget.currentText()

    def set_value(self, v):
        idx = self.widget.findText(str(v) if v is not None else "")
        if idx >= 0:
            self.widget.setCurrentIndex(idx)


class MultiStringField(FieldRow):
    """Editable list of strings (for tools array etc)."""

    def __init__(self, key: str, widget: QWidget, section: str | None = None):
        super().__init__(key, widget, section)
        self.list_widget = widget.findChild(QListWidget)
        self.edit_widget = widget.findChild(QLineEdit)
        self._items: list[str] = []

    def get_value(self):
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def set_value(self, v):
        self.list_widget.clear()
        if v:
            for item in v:
                self.list_widget.addItem(str(item))
            self._items = list(v)

    def add_item(self):
        text = self.edit_widget.text().strip()
        if text:
            self.list_widget.addItem(text)
            self.edit_widget.clear()

    def remove_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))


def make_tool_list_widget() -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    lst = QListWidget()
    lst.setMaximumHeight(120)
    editor = QLineEdit()
    editor.setPlaceholderText("输入工具名后点击添加")
    btn_row = QHBoxLayout()
    add_btn = QPushButton("添加")
    rm_btn = QPushButton("移除选中")
    btn_row.addWidget(add_btn)
    btn_row.addWidget(rm_btn)
    btn_row.addStretch()
    layout.addWidget(lst)
    layout.addWidget(editor)
    layout.addLayout(btn_row)

    def on_add():
        text = editor.text().strip()
        if text:
            lst.addItem(text)
            editor.clear()

    def on_rm():
        for item in lst.selectedItems():
            lst.takeItem(lst.row(item))

    add_btn.clicked.connect(on_add)
    rm_btn.clicked.connect(on_rm)
    return w


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Section page builders
# ══════════════════════════════════════════════════════════════════════════════

def make_section_page(title: str, fields: list[FieldRow], note: str = "") -> QWidget:
    """Build a scrollable form page with the given fields.

    Each FieldRow should already be connected to the right widget.
    We store them as attributes for later save/load.
    """
    page = QWidget()
    page._fields = fields
    layout = QVBoxLayout(page)

    if title:
        lbl = QLabel(f"<h2>{title}</h2>")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

    if note:
        nl = QLabel(f"<p style='color:#888;'>{note}</p>")
        nl.setWordWrap(True)
        layout.addWidget(nl)

    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    for f in fields:
        label_text = f.key
        if isinstance(f, SecretField):
            label_text += " (密钥)"
        label = QLabel(label_text)
        label.setToolTip(f.key)
        form.addRow(label, f.widget)

    layout.addLayout(form)
    layout.addStretch()
    return page


def build_llm_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        StringField("llm_url", QLineEdit()),
        StringField("llm_model", QLineEdit()),
        StringField("local_model", QLineEdit()),
    ]
    for f in fields:
        f.set_value(cfg.get(f.key, ""))

    page = QWidget()
    page._fields = fields
    layout = QVBoxLayout(page)
    lbl = QLabel("<h2>LLM 路由</h2>")
    layout.addWidget(lbl)
    note = QLabel(
        "<p style='color:#a6adc8;'>配置对话模型和服务地址。API 密钥请在「密钥」页面管理。</p>"
    )
    note.setWordWrap(True)
    layout.addWidget(note)

    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    for f in fields:
        form.addRow(f.key, f.widget)
    layout.addLayout(form)
    layout.addStretch()
    return page, fields


def build_tts_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        ChoiceField("tts_backend", QComboBox(), ["minimax", "cartesia"]),
        FloatField("tts_volume", QDoubleSpinBox(), mini=0.0, maxi=2.0, step=0.1, decimals=1),
        StringField("tts_voice_id", QLineEdit()),
        IntField("tts_sample_rate", QSpinBox(), mini=8000, maxi=48000),
        ChoiceField("minimax_model", QComboBox(), ["speech-2.8-turbo", "speech-2.8-pro"]),
        IntField("minimax_sample_rate", QSpinBox(), mini=8000, maxi=48000),
        FloatField("minimax_tts_speed", QDoubleSpinBox(), mini=0.5, maxi=2.0, step=0.1, decimals=1),
        FloatField("minimax_tts_buffer_seconds", QDoubleSpinBox(), mini=0.0, maxi=2.0, step=0.05, decimals=2),
        IntField("tts_stream_chunk_chars", QSpinBox(), mini=5, maxi=100),
        IntField("tts_stream_sentence_min_chars", QSpinBox(), mini=2, maxi=50),
    ]
    for f in fields:
        f.set_value(cfg.get(f.key, ""))
    page = make_section_page("TTS — 文本转语音", fields,
                             note="配置语音合成后端、音量和语速。API 密钥请在「密钥」页面管理。")
    return page, fields


def build_aec_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("aec", {})
    fields = [
        BoolField("enabled", QCheckBox("启用回声消除"), "aec"),
        IntField("delay_ms", QSpinBox(), mini=0, maxi=500, section="aec"),
        IntField("ns_level", QSpinBox(), mini=0, maxi=4, section="aec"),
    ]
    for f in fields:
        f.set_value(section.get(f.key))
    page = make_section_page("AEC — 声学回声消除", fields,
                             note="通过 WebRTC AEC 消除 TTS 外放被麦克风拾取的回声。")
    return page, fields


def build_stt_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        StringField("stt_model_dir", QLineEdit()),
        StringField("stt_refine_model", QLineEdit()),
        StringField("cognition_model", QLineEdit()),
        IntField("cognition_eval_interval", QSpinBox(), mini=0, maxi=100),
        StringField("emotion_model", QLineEdit()),
        ChoiceField("stt_refine_mode", QComboBox(), ["separate", "inline", "none"]),
        FloatField("stt_refine_stable_seconds", QDoubleSpinBox(), mini=0.1, maxi=10.0, step=0.1, decimals=1),
        FloatField("stt_pool_tick_seconds", QDoubleSpinBox(), mini=0.01, maxi=1.0, step=0.01, decimals=3),
        IntField("stt_refine_max_tokens", QSpinBox(), mini=16, maxi=1024),
        BoolField("stt_skip_short_refine", QCheckBox("跳过短文本精炼")),
        IntField("stt_skip_short_refine_max_chars", QSpinBox(), mini=0, maxi=100),
        BoolField("stt_pause_during_tts", QCheckBox("TTS 播放时暂停麦克风")),
    ]
    c = cfg.get("stt", cfg)
    for f in fields:
        f.set_value(cfg.get(f.key, c.get(f.key, "")))
    page = make_section_page("STT — 语音转文字", fields, note="配置语音识别模型、精炼模式和认知/情绪评估。")
    return page, fields


def build_memory_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        ChoiceField("memory_backend", QComboBox(), ["mem0", "none", "kokoromemo"]),
        StringField("kokoromo_url", QLineEdit()),
        StringField("kokoromo_dir", QLineEdit()),
    ]
    c = cfg.get("memory", cfg)
    for f in fields:
        f.set_value(cfg.get(f.key, c.get(f.key, "")))
    page = make_section_page("记忆后端", fields, note="配置记忆存储后端类型和相关服务地址。")
    return page, fields


def build_portrait_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        StringField("portrait_overlay_host", QLineEdit()),
        IntField("portrait_overlay_port", QSpinBox(), mini=1024, maxi=65535),
        FloatField("portrait_decision_interval", QDoubleSpinBox(), mini=0.0, maxi=60.0, step=0.5, decimals=1),
        FloatField("portrait_decay_seconds", QDoubleSpinBox(), mini=0.0, maxi=600.0, step=5.0, decimals=0),
        BoolField("portrait_debug_overlay", QCheckBox("显示冲动值调试信息")),
        BoolField("portrait_click_through", QCheckBox("鼠标点击穿透")),
        StringField("portrait_model", QLineEdit()),
    ]
    for f in fields:
        f.set_value(cfg.get(f.key, ""))
    page = make_section_page("立绘覆盖层", fields, note="透明立绘窗口 (PySide6)，显示角色表情。")
    return page, fields


def build_subtitle_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("subtitle", {})
    fields = [
        StringField("font_color", QLineEdit(), "subtitle"),
        StringField("stroke_color", QLineEdit(), "subtitle"),
        IntField("font_size", QSpinBox(), mini=8, maxi=120, section="subtitle"),
        StringField("subtitle_host", QLineEdit(), "subtitle"),
        IntField("subtitle_port", QSpinBox(), mini=1024, maxi=65535, section="subtitle"),
    ]
    for f in fields:
        f.set_value(section.get(f.key, cfg.get(f.key, "")))
    page = make_section_page("字幕覆盖层", fields, note="透明字幕窗口，流式显示 LLM 输出。")
    return page, fields


def build_subtitle_stt_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("subtitle_stt", {})
    fields = [
        StringField("font_color", QLineEdit(), "subtitle_stt"),
        StringField("stroke_color", QLineEdit(), "subtitle_stt"),
        IntField("font_size", QSpinBox(), mini=8, maxi=120, section="subtitle_stt"),
        StringField("btn_color", QLineEdit(), "subtitle_stt"),
        StringField("subtitle_host", QLineEdit(), "subtitle_stt"),
        IntField("subtitle_port", QSpinBox(), mini=1024, maxi=65535, section="subtitle_stt"),
    ]
    for f in fields:
        f.set_value(section.get(f.key, ""))
    page = make_section_page("STT 字幕 (独立)", fields, note="STT 专用字幕窗口，独立端口。")
    return page, fields


def build_vision_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        ChoiceField("vision_backend", QComboBox(), ["dashscope", "ollama"]),
        StringField("vision_model", QLineEdit()),
        IntField("vision_max_pixels", QSpinBox(), mini=0, maxi=9999999),
    ]
    c = cfg.get("vision", cfg)
    for f in fields:
        f.set_value(cfg.get(f.key, c.get(f.key, "")))
    page = make_section_page("视觉 / 屏幕识别", fields,
                             note="配置截图分析后端和模型。API 密钥请在「密钥」页面管理。")
    return page, fields


def build_edge_cache_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("edge_page_cache", {})
    fields = [
        BoolField("enabled", QCheckBox("启用 Edge 网页缓存"), "edge_page_cache"),
        FloatField("interval_seconds", QDoubleSpinBox(), mini=0.5, maxi=300.0, step=0.5, decimals=1, section="edge_page_cache"),
        StringField("devtools_host", QLineEdit(), "edge_page_cache"),
        IntField("devtools_port", QSpinBox(), mini=1, maxi=65535, section="edge_page_cache"),
        StringField("cache_file", QLineEdit(), "edge_page_cache"),
        IntField("max_chars", QSpinBox(), mini=100, maxi=999999, section="edge_page_cache"),
        FloatField("request_timeout", QDoubleSpinBox(), mini=0.5, maxi=60.0, step=0.5, decimals=1, section="edge_page_cache"),
    ]
    for f in fields:
        f.set_value(section.get(f.key))
    page = make_section_page("Edge 网页缓存", fields, note="周期性读取当前 Edge 标签页正文。")
    return page, fields


def build_screen_watch_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("screen_watch", {})
    fields = [
        BoolField("enabled", QCheckBox("启用屏幕监控"), "screen_watch"),
        FloatField("watch_interval", QDoubleSpinBox(), mini=1.0, maxi=600.0, step=1.0, decimals=0, section="screen_watch"),
        FloatField("interest_threshold", QDoubleSpinBox(), mini=0.0, maxi=100.0, step=5.0, decimals=0, section="screen_watch"),
        IntField("vision_timeout", QSpinBox(), mini=5, maxi=300, section="screen_watch"),
    ]
    for f in fields:
        f.set_value(section.get(f.key))
    page = make_section_page("屏幕监控", fields, note="周期性截图并通过视觉 API 分析内容。")
    return page, fields


def build_impulse_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("impulse", {})
    fields = [
        BoolField("enabled", QCheckBox("启用主动搭话"), "impulse"),
        IntField("max_plans", QSpinBox(), mini=1, maxi=20, section="impulse"),
        IntField("min_plans", QSpinBox(), mini=0, maxi=10, section="impulse"),
        StringField("planning_model", QLineEdit(), "impulse"),
        IntField("screen_timeout", QSpinBox(), mini=5, maxi=300, section="impulse"),
        FloatField("empty_plan_retry_seconds", QDoubleSpinBox(), mini=1.0, maxi=600.0, step=5.0, decimals=0, section="impulse"),
        BoolField("log_plan_table", QCheckBox("控制台输出计划表"), "impulse"),
        FloatField("memory_cooldown_seconds", QDoubleSpinBox(), mini=0.0, maxi=86400.0, step=300.0, decimals=0, section="impulse"),
        FloatField("memory_date_score", QDoubleSpinBox(), mini=0.0, maxi=100.0, step=5.0, decimals=0, section="impulse"),
        FloatField("memory_lookup_score", QDoubleSpinBox(), mini=0.0, maxi=100.0, step=5.0, decimals=0, section="impulse"),
        StringField("memory_lookup_query", QLineEdit(), "impulse"),
        BoolField("memory_events_enabled", QCheckBox("开启周期性记忆事件轮询"), "impulse"),
        FloatField("memory_check_interval", QDoubleSpinBox(), mini=10.0, maxi=3600.0, step=30.0, decimals=0, section="impulse"),
    ]
    for f in fields:
        f.set_value(section.get(f.key))
    page = make_section_page("主动搭话调度器 (Impulse)", fields, note="LLM 规划式主动搭话机制。")
    return page, fields


def build_bilibili_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("bilibili_live", {})
    fields = [
        BoolField("enabled", QCheckBox("连接 Bilibili"), "bilibili_live"),
        BoolField("live_mode", QCheckBox("直播模式"), "bilibili_live"),
        IntField("room_id", QSpinBox(), mini=0, maxi=999999999, section="bilibili_live"),
        FloatField("buffer_max_age", QDoubleSpinBox(), mini=5.0, maxi=600.0, step=5.0, decimals=0, section="bilibili_live"),
        FloatField("reconnect_delay", QDoubleSpinBox(), mini=0.5, maxi=120.0, step=0.5, decimals=1, section="bilibili_live"),
    ]
    for f in fields:
        f.set_value(section.get(f.key))
    page = make_section_page("Bilibili 直播间弹幕", fields, note="读取弹幕并在空闲时回复。")
    return page, fields


def build_mem0_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    mem0 = cfg.get("mem0", {})
    fields = [
        # llm subsection
        StringField("mem0_llm_provider", QLineEdit()),
        StringField("mem0_llm_base_url", QLineEdit()),
        StringField("mem0_llm_model", QLineEdit()),
        # embedder subsection
        StringField("mem0_embedder_provider", QLineEdit()),
        StringField("mem0_embedder_model", QLineEdit()),
        IntField("mem0_embedding_dims", QSpinBox(), mini=64, maxi=4096),
        # lifecycle subsection
        ChoiceField("mem0_importance_mode", QComboBox(), ["auto", "always"]),
        FloatField("mem0_search_threshold", QDoubleSpinBox(), mini=0.0, maxi=1.0, step=0.05, decimals=2),
        IntField("mem0_search_top_k", QSpinBox(), mini=1, maxi=100),
    ]

    values = {
        "mem0_llm_provider": mem0.get("llm", {}).get("provider", "ollama"),
        "mem0_llm_base_url": mem0.get("llm", {}).get("base_url", "http://127.0.0.1:11434"),
        "mem0_llm_model": mem0.get("llm", {}).get("model", "qwen2.5:1.5b"),
        "mem0_embedder_provider": mem0.get("embedder", {}).get("provider", "fastembed"),
        "mem0_embedder_model": mem0.get("embedder", {}).get("model", "BAAI/bge-small-zh-v1.5"),
        "mem0_embedding_dims": mem0.get("embedder", {}).get("embedding_dims", 512),
        "mem0_importance_mode": mem0.get("lifecycle", {}).get("importance_mode", "auto"),
        "mem0_search_threshold": mem0.get("lifecycle", {}).get("search_threshold", 0.3),
        "mem0_search_top_k": mem0.get("lifecycle", {}).get("search_top_k", 8),
    }
    for f in fields:
        f.set_value(values.get(f.key, ""))

    page = make_section_page("Mem0 记忆服务", fields, note="配置内部向量记忆库的 LLM、嵌入和生命周期参数。")
    return page, fields


def build_tool_calling_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    section = cfg.get("tool_calling", {})
    tool_widget = make_tool_list_widget()
    fields = [
        BoolField("enabled", QCheckBox("启用工具调用"), "tool_calling"),
        MultiStringField("tools", tool_widget, "tool_calling"),
        IntField("max_iterations", QSpinBox(), mini=1, maxi=50, section="tool_calling"),
        FloatField("tool_timeout", QDoubleSpinBox(), mini=1.0, maxi=300.0, step=5.0, decimals=0, section="tool_calling"),
    ]
    # Replace the tools field widget
    for f in fields:
        if f.key == "tools":
            f.set_value(section.get("tools", ["look_at_screen", "search_memory"]))
        else:
            f.set_value(section.get(f.key))

    # Build custom page for tool calling (since we have a special widget)
    page = QWidget()
    page._fields = fields
    layout = QVBoxLayout(page)

    lbl = QLabel("<h2>工具调用</h2>")
    layout.addWidget(lbl)

    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.addRow("enabled", fields[0].widget)
    form.addRow("tools", tool_widget)
    form.addRow("max_iterations", fields[2].widget)
    form.addRow("tool_timeout", fields[3].widget)
    layout.addLayout(form)
    layout.addStretch()
    return page, fields


# ── Characters page ──────────────────────────────────────────────────────────

class CharEditPanel(QWidget):
    """Inline editor for a single character."""

    def __init__(self, char_id: str, data: dict):
        super().__init__()
        self.char_id = char_id
        self.data = data
        layout = QVBoxLayout(self)

        fields = ["name", "description", "personality", "background",
                   "greeting", "scene", "expression_calibration", "tts_voice_id"]
        self.editors: dict[str, QPlainTextEdit | QLineEdit] = {}

        form = QFormLayout()
        for key in fields:
            val = data.get(key, "")
            if key in ("personality", "background", "description", "scene",
                       "expression_calibration"):
                editor = QPlainTextEdit()
                editor.setPlainText(val)
                editor.setMaximumHeight(120 if key in (
                    "description", "greeting", "tts_voice_id", "scene"
                ) else 200)
            else:
                editor = QLineEdit()
                editor.setText(str(val))
            self.editors[key] = editor
            form.addRow(key, editor)

        # Example dialogue
        ed_lbl = QLabel("example_dialogue")
        self.dialogue_edit = QPlainTextEdit()
        self.dialogue_edit.setPlainText(data.get("example_dialogue", ""))
        self.dialogue_edit.setMaximumHeight(250)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_widget.setLayout(QVBoxLayout())
        scroll_widget.layout().addLayout(form)
        scroll_widget.layout().addWidget(ed_lbl)
        scroll_widget.layout().addWidget(self.dialogue_edit)
        scroll_widget.layout().addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

    def get_data(self) -> dict:
        result = {}
        for key, editor in self.editors.items():
            if isinstance(editor, QPlainTextEdit):
                result[key] = editor.toPlainText()
            else:
                result[key] = editor.text()
        result["example_dialogue"] = self.dialogue_edit.toPlainText()
        # preserve keys not in the editor
        for k, v in self.data.items():
            if k not in result:
                result[k] = v
        return result


def build_characters_page() -> tuple[QWidget, list[FieldRow]]:
    """Characters page uses a tab-like panel, no standard FieldRows."""
    page = QWidget()
    layout = QVBoxLayout(page)

    lbl = QLabel("<h2>角色管理</h2>")
    layout.addWidget(lbl)
    note = QLabel("编辑各角色的立绘、性格、对话风格等设定。")
    note.setWordWrap(True)
    layout.addWidget(note)

    chars = load_all_characters()
    char_ids = list(chars.keys())
    if not char_ids:
        layout.addWidget(QLabel("未找到角色文件。"))
        page._fields = []
        return page, []

    from PySide6.QtWidgets import QTabWidget
    tabs = QTabWidget()
    editors: dict[str, CharEditPanel] = {}
    for cid in char_ids:
        panel = CharEditPanel(cid, chars[cid])
        editors[cid] = panel
        tabs.addTab(panel, chars[cid].get("name", cid))
    layout.addWidget(tabs)
    page._editors = editors
    page._fields = []  # no standard fields
    return page, []


# ── Prompts page ─────────────────────────────────────────────────────────────

class PromptEditorPanel(QWidget):
    """Tree-like navigation for prompt templates."""

    def __init__(self, prompts_data: dict):
        super().__init__()
        self.prompts_data = prompts_data
        layout = QVBoxLayout(self)

        lbl = QLabel("<h2>提示词模板</h2>")
        layout.addWidget(lbl)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: category list
        self.cat_list = QListWidget()
        self.cat_list.setMaximumWidth(200)
        splitter.addWidget(self.cat_list)

        # Right: key list + editor
        right_w = QWidget()
        right_layout = QVBoxLayout(right_w)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.key_list = QListWidget()
        self.key_list.setMaximumHeight(150)
        right_layout.addWidget(QLabel("选择模板键："))
        right_layout.addWidget(self.key_list)

        self.editor = QPlainTextEdit()
        right_layout.addWidget(QLabel("模板内容："))
        right_layout.addWidget(self.editor)

        splitter.addWidget(right_w)
        splitter.setSizes([200, 500])
        layout.addWidget(splitter)

        # Store key path for current editing
        self._current_path: tuple = ()

        self._build_categories()
        self.cat_list.currentItemChanged.connect(self._on_cat_changed)
        self.key_list.currentItemChanged.connect(self._on_key_changed)

    def _build_categories(self):
        self.cat_map: dict[str, dict] = {}
        for key, val in self.prompts_data.items():
            self.cat_list.addItem(key)
            self.cat_map[key] = val if isinstance(val, dict) else {key: val}
        if self.cat_list.count():
            self.cat_list.setCurrentRow(0)

    def _on_cat_changed(self, current, _previous):
        self.key_list.clear()
        if not current:
            return
        cat = self.cat_map.get(current.text(), {})
        if not isinstance(cat, dict):
            cat = {current.text(): cat}
        self._flat_keys(cat, prefix="")
        if self.key_list.count():
            self.key_list.setCurrentRow(0)

    def _flat_keys(self, d: dict, prefix: str):
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                self.key_list.addItem(f"{path}/")
            else:
                self.key_list.addItem(path)

    def _on_key_changed(self, current, _previous):
        if not current:
            return
        path = current.text().rstrip("/")
        parts = path.split(".")
        val = self.prompts_data
        try:
            for p in parts:
                val = val[p]
        except (KeyError, TypeError):
            self.editor.setPlainText("")
            return
        if isinstance(val, dict):
            self.editor.setPlainText(json.dumps(val, ensure_ascii=False, indent=2))
        else:
            self.editor.setPlainText(str(val))
        self._current_path = tuple(parts)

    def collect_data(self) -> dict:
        """Save editor contents back to prompts_data."""
        if self._current_path:
            val = self.editor.toPlainText()
            d = self.prompts_data
            for p in self._current_path[:-1]:
                d = d[p]
            key = self._current_path[-1]
            # Try to parse as JSON if it ends with /
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                d[key] = val
        return self.prompts_data


def build_prompts_page(prompts_data: dict) -> tuple[QWidget, list[FieldRow]]:
    page = PromptEditorPanel(prompts_data)
    page._fields = []
    return page, []
    # — note: prompts saving is handled via collect_data()


# ── Secrets page ─────────────────────────────────────────────────────────────

def build_secrets_page(cfg: dict) -> tuple[QWidget, list[FieldRow]]:
    fields = [
        SecretField("deepseek_api_key", QLineEdit()),
        SecretField("minimax_api_key", QLineEdit()),
        SecretField("cartesia_api_key", QLineEdit()),
        SecretField("vision_api_key", QLineEdit()),
        SecretField("charglm_api_key", QLineEdit()),
        StringField("tts_voice_id", QLineEdit()),
    ]
    for f in fields:
        f.set_value(cfg.get(f.key, ""))
    page = make_section_page("密钥管理 (config.json)", fields,
                             note="这些密钥独立存储在 config.json 中，不提交到 git。")
    return page, fields


# ── Per-character config override page ───────────────────────────────────────

def build_per_char_config_page() -> tuple[QWidget, list[FieldRow]]:
    page = QWidget()
    layout = QVBoxLayout(page)
    lbl = QLabel("<h2>角色专用配置覆盖</h2>")
    layout.addWidget(lbl)
    note = QLabel("每个角色可有独立的 config.toml，覆盖全局 LLM 配置。")
    note.setWordWrap(True)
    layout.addWidget(note)

    chars = load_all_characters()
    editors: dict[str, dict[str, QLineEdit | QCheckBox]] = {}

    for cid, data in chars.items():
        gb = QGroupBox(data.get("name", cid))
        fl = QFormLayout(gb)
        char_cfg = load_per_character_toml(cid)
        eds = {}
        for key in ("llm_model", "llm_url"):
            le = QLineEdit()
            le.setText(char_cfg.get(key, ""))
            fl.addRow(key, le)
            eds[key] = le
        editors[cid] = eds
        layout.addWidget(gb)

    layout.addStretch()
    page._editors = editors
    page._fields = []
    return page, []


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Main Window
# ══════════════════════════════════════════════════════════════════════════════

class ConfigEditorWindow(QMainWindow):
    STYLE = """
    QMainWindow { background: #1e1e2e; }
    QWidget { color: #cdd6f4; font-size: 13px; }
    QLabel { color: #cdd6f4; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
        background: #313244;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 4px 8px;
        color: #cdd6f4;
    }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus {
        border-color: #89b4fa;
    }
    QCheckBox { spacing: 8px; }
    QCheckBox::indicator {
        width: 18px; height: 18px;
        border: 2px solid #45475a;
        border-radius: 3px;
        background: #313244;
    }
    QCheckBox::indicator:checked {
        background: #89b4fa;
        border-color: #89b4fa;
    }
    QPushButton {
        background: #45475a;
        border: none;
        border-radius: 6px;
        padding: 6px 16px;
        color: #cdd6f4;
        font-weight: bold;
    }
    QPushButton:hover { background: #585b70; }
    QPushButton:pressed { background: #313244; }
    QPushButton#save_btn {
        background: #89b4fa;
        color: #1e1e2e;
        padding: 8px 24px;
        font-size: 14px;
    }
    QPushButton#save_btn:hover { background: #b4d0fb; }
    QListWidget {
        background: #181825;
        border: none;
        border-right: 1px solid #313244;
        outline: none;
        font-size: 13px;
    }
    QListWidget::item {
        padding: 10px 16px;
        border-left: 3px solid transparent;
    }
    QListWidget::item:selected {
        background: #313244;
        border-left: 3px solid #89b4fa;
        color: #89b4fa;
    }
    QListWidget::item:hover:!selected {
        background: #252536;
    }
    QGroupBox {
        border: 1px solid #45475a;
        border-radius: 6px;
        margin-top: 12px;
        padding: 16px 8px 8px 8px;
        font-weight: bold;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
    }
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: #181825;
        width: 10px;
    }
    QScrollBar::handle:vertical {
        background: #45475a;
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QTabWidget::pane { border: 1px solid #45475a; border-radius: 4px; }
    QTabBar::tab {
        background: #313244; padding: 8px 16px; border: none;
        border-top-left-radius: 4px; border-top-right-radius: 4px;
    }
    QTabBar::tab:selected { background: #45475a; color: #89b4fa; }
    QSplitter::handle { background: #313244; width: 2px; }
    QStatusBar { background: #181825; border-top: 1px solid #313244; color: #6c7086; }
    QToolBar {
        background: #181825;
        border-bottom: 1px solid #313244;
        spacing: 8px;
        padding: 4px;
    }
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alice 配置编辑器")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 820)
        self.setStyleSheet(self.STYLE)

        # ── Load all config data ──
        self.toml_cfg = load_toml(CONFIG_TOML)
        self.json_cfg = load_json_file(CONFIG_JSON)
        self.merged_cfg = {**self.json_cfg, **self.toml_cfg}

        self.prompts_data = load_json_file(PROMPTS_JSON)
        self.characters_data = load_json_file(CHARACTERS_JSON)
        self.per_char_editors: dict = {}

        # ── State ──
        self.pages: dict[str, QWidget] = {}
        self.page_fields: dict[str, list[FieldRow]] = {}
        self.page_builders: dict[str, callable] = {}

        self._init_ui()
        self._build_pages()
        self._show_page(0)

        # Update status
        self.statusBar().showMessage("就绪 — 编辑配置后点击「保存」")

    # ── UI setup ──

    def _init_ui(self):
        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        title_lbl = QLabel("  Alice 配置编辑器")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        title_lbl.setStyleSheet("color: #89b4fa; padding: 4px 8px;")
        toolbar.addWidget(title_lbl)

        toolbar.addSeparator()

        save_btn = QPushButton("保存所有配置")
        save_btn.setObjectName("save_btn")
        save_btn.clicked.connect(self._do_save)
        toolbar.addWidget(save_btn)

        toolbar.addSeparator()

        reload_btn = QPushButton("🔄 重新加载")
        reload_btn.clicked.connect(self._do_reload)
        toolbar.addWidget(reload_btn)

        # Central widget: sidebar + content
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar navigation
        self.nav = QListWidget()
        self.nav.setFixedWidth(200)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        main_layout.addWidget(self.nav)

        # Content area
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self._show_page)

        # Status bar
        self.statusBar().showMessage("加载配置...")

    def _build_pages(self):
        """Register all config pages."""
        cat_nav = [
            ("LLM", "llm", build_llm_page),
            ("TTS", "tts", build_tts_page),
            ("STT", "stt", build_stt_page),
            ("AEC", "aec", build_aec_page),
            ("记忆", "memory", build_memory_page),
            ("立绘", "portrait", build_portrait_page),
            ("字幕", "subtitle", build_subtitle_page),
            ("STT字幕", "subtitle_stt", build_subtitle_stt_page),
            ("视觉", "vision", build_vision_page),
            ("Edge缓存", "edge_cache", build_edge_cache_page),
            ("屏幕监控", "screen_watch", build_screen_watch_page),
            ("主动搭话", "impulse", build_impulse_page),
            ("B站直播", "bilibili", build_bilibili_page),
            ("Mem0", "mem0", build_mem0_page),
            ("工具调用", "tool_calling", build_tool_calling_page),
            ("密钥", "secrets", build_secrets_page),
            ("角色编辑", "characters", build_characters_page),
            ("提示词", "prompts", build_prompts_page),
            ("角色配置覆盖", "per_char_config", build_per_char_config_page),
        ]

        nav_map = {}

        for i, (nav_name, page_id, builder) in enumerate(cat_nav):
            item = QListWidgetItem(nav_name)
            self.nav.addItem(item)
            nav_map[i] = (page_id, builder)

        self._nav_map = nav_map

    def _show_page(self, index: int):
        if index < 0 or index >= len(self._nav_map):
            return

        page_id, builder = self._nav_map.get(index, (None, None))
        if page_id is None:
            return

        # Build pages lazily
        if page_id not in self.pages:
            if page_id == "secrets":
                page, fields = builder(self.json_cfg)
            elif page_id == "prompts":
                page, fields = builder(self.prompts_data)
            elif page_id == "characters":
                page, fields = builder()
            elif page_id == "per_char_config":
                page, fields = builder()
            elif page_id in ("subtitle", "subtitle_stt", "edge_cache", "screen_watch",
                             "impulse", "bilibili", "tool_calling", "aec"):
                page, fields = builder(self.toml_cfg)
            else:
                page, fields = builder(self.merged_cfg)

            # Wrap in scroll area
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
            self.pages[page_id] = scroll
            self.page_fields[page_id] = fields

        self.stack.setCurrentWidget(self.pages[page_id])

    # ── Save logic ──

    def _collect_page_values(self, page_id: str) -> dict | None:
        """Read values from page fields. Returns None if page not loaded."""
        if page_id not in self.pages:
            return None

        scroll = self.pages[page_id]
        page = scroll.widget()
        fields = self.page_fields.get(page_id, [])

        if page_id == "characters":
            if hasattr(page, "_editors"):
                for cid, editor in page._editors.items():
                    save_character(cid, editor.get_data())
            return {}

        if page_id == "prompts":
            if isinstance(page, PromptEditorPanel):
                page.collect_data()
            return {}

        if page_id == "per_char_config":
            if hasattr(page, "_editors"):
                for cid, eds in page._editors.items():
                    data = {}
                    for k, w in eds.items():
                        if isinstance(w, QLineEdit):
                            v = w.text()
                            if v:
                                data[k] = v
                    save_per_character_toml(cid, data)
            return {}

        if page_id == "mem0":
            results = {}
            for f in fields:
                val = f.get_value()
                key = f.key
                if key.startswith("mem0_llm_"):
                    results.setdefault("mem0_llm", {})[key[9:]] = val
                elif key.startswith("mem0_embedder_"):
                    results.setdefault("mem0_embedder", {})[key[13:]] = val
                elif key.startswith("mem0_"):
                    results.setdefault("mem0_lifecycle", {})[key[5:]] = val
            return results

        results = {}
        tomli_keys = {"subtitle", "subtitle_stt", "edge_cache", "screen_watch",
                      "impulse", "bilibili", "tool_calling", "aec"}
        for f in fields:
            val = f.get_value()
            # Map mem0 fields back into sub-dicts
            key = f.key

            # Handle section-prefixed keys
            if f.section:
                results.setdefault(f.section, {})[key] = val
            else:
                results[key] = val

        return results

    def _do_save(self):
        """Save all pages to respective config files."""
        try:
            # ── Build updated toml config ──
            toml_updates = {}
            toml_sections = {
                "llm", "tts", "stt", "aec", "memory", "portrait",
                "subtitle", "subtitle_stt", "vision", "edge_cache",
                "screen_watch", "impulse", "bilibili", "tool_calling",
            }

            for pid in toml_sections:
                vals = self._collect_page_values(pid)
                if vals is None:
                    continue
                toml_updates.update(vals)

            # Handle mem0 separately
            mem0_vals = self._collect_page_values("mem0")
            if mem0_vals:
                mem0_merged = {}
                for section_key, section_vals in mem0_vals.items():
                    mapped = section_key.replace("mem0_", "")
                    mem0_merged[mapped] = section_vals
                if mem0_merged:
                    toml_updates["mem0"] = mem0_merged

            # Write config.toml
            write_config_toml(CONFIG_TOML, toml_updates)

            # ── Secrets page ──
            secrets_vals = self._collect_page_values("secrets")
            if secrets_vals:
                # Only save non-empty secrets
                new_secrets = {}
                for k, v in secrets_vals.items():
                    if v:
                        new_secrets[k] = v
                # Preserve any existing keys not on the secrets page
                for k, v in self.json_cfg.items():
                    if k not in new_secrets:
                        new_secrets[k] = v
                save_json_file(CONFIG_JSON, new_secrets)
                self.json_cfg = new_secrets

            # ── Prompts ──
            if "prompts" in self.pages:
                save_json_file(PROMPTS_JSON, self.prompts_data)

            self.statusBar().showMessage("✅ 所有配置已保存", 5000)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存配置时出错：\n{e}")
            self.statusBar().showMessage(f"❌ 保存失败: {e}")

    def _do_reload(self):
        """Reload all config from disk and refresh UI."""
        reply = QMessageBox.question(
            self, "重新加载",
            "重新加载将丢弃未保存的更改。确定吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.toml_cfg = load_toml(CONFIG_TOML)
        self.json_cfg = load_json_file(CONFIG_JSON)
        self.merged_cfg = {**self.json_cfg, **self.toml_cfg}
        self.prompts_data = load_json_file(PROMPTS_JSON)

        # Clear cached pages
        self.pages.clear()
        self.page_fields.clear()

        # Rebuild
        self._build_pages()
        self._show_page(0)
        self.statusBar().showMessage("🔄 已重新加载配置", 3000)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Set dark palette
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#313244"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#45475a"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#89b4fa"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1e1e2e"))
    app.setPalette(palette)

    window = ConfigEditorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
