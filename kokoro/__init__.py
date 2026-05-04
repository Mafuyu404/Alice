"""
KokoroMemo 核心库 — 所有功能模块的集中入口。

提供角色管理、语音识别（STT）、对话池、语音合成（TTS）、
记忆后端等子模块的统一导入接口。

用法:
    from kokoro import stt, tts, pool, memory, config, character
"""

from . import config
from . import character
from . import stt
from . import pool
from . import tts
from . import memory
from . import vision
