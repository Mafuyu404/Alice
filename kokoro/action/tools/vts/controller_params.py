"""VTS parameter names and default expression mappings."""

from __future__ import annotations

PLUGIN_NAME = "alice-vts"
DEVELOPER = "Alice"
TOKEN_PATH = "./vts_token.txt"

# Tracking parameter name constants
PARAM_EYE_OPEN_L = "EyeOpenLeft"
PARAM_EYE_OPEN_R = "EyeOpenRight"
PARAM_MOUTH_OPEN = "MouthOpen"
PARAM_MOUTH_SMILE = "MouthSmile"
PARAM_BROWS = "Brows"
PARAM_FACE_ANGLE_X = "FaceAngleX"
PARAM_FACE_ANGLE_Y = "FaceAngleY"
PARAM_FACE_ANGLE_Z = "FaceAngleZ"
PARAM_FACE_POS_Z = "FacePositionZ"
PARAM_FACE_POS_X = "FacePositionX"
PARAM_FACE_POS_Y = "FacePositionY"
PARAM_MOCOPI_BODY_ANGLE_X = "MocopiBodyAngleX"
PARAM_MOCOPI_BODY_ANGLE_Y = "MocopiBodyAngleY"
PARAM_MOCOPI_BODY_ANGLE_Z = "MocopiBodyAngleZ"
PARAM_MOCOPI_BODY_POS_X = "MocopiBodyPositionX"
PARAM_MOCOPI_BODY_POS_Y = "MocopiBodyPositionY"
PARAM_MOCOPI_BODY_POS_Z = "MocopiBodyPositionZ"
PARAM_EYE_LEFT_X = "EyeLeftX"
PARAM_EYE_LEFT_Y = "EyeLeftY"
PARAM_EYE_RIGHT_X = "EyeRightX"
PARAM_EYE_RIGHT_Y = "EyeRightY"

_ALL_VALID_PARAMS = {
    PARAM_EYE_OPEN_L, PARAM_EYE_OPEN_R, PARAM_MOUTH_OPEN,
    PARAM_MOUTH_SMILE, PARAM_BROWS, PARAM_FACE_ANGLE_X,
    PARAM_FACE_ANGLE_Y, PARAM_FACE_ANGLE_Z, PARAM_FACE_POS_X, PARAM_FACE_POS_Y, PARAM_FACE_POS_Z,
    PARAM_MOCOPI_BODY_ANGLE_X, PARAM_MOCOPI_BODY_ANGLE_Y, PARAM_MOCOPI_BODY_ANGLE_Z,
    PARAM_MOCOPI_BODY_POS_X, PARAM_MOCOPI_BODY_POS_Y, PARAM_MOCOPI_BODY_POS_Z,
    PARAM_EYE_LEFT_X, PARAM_EYE_LEFT_Y, PARAM_EYE_RIGHT_X,
    PARAM_EYE_RIGHT_Y,
}

# ── Default expression presets ──────────────────────────────────────────────

DEFAULT_EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral":   {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.5,
                  PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1},
    "happy":     {PARAM_MOUTH_SMILE: 0.7, PARAM_BROWS: 0.3},
    "smile":     {PARAM_MOUTH_SMILE: 0.5, PARAM_BROWS: 0.4},
    "sad":       {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.8, PARAM_MOUTH_OPEN: 0.1},
    "angry":     {PARAM_BROWS: 1, PARAM_MOUTH_SMILE: 0, PARAM_MOUTH_OPEN: 0.15},
    "surprised": {PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1,
                  PARAM_MOUTH_OPEN: 0.6, PARAM_BROWS: 0.8},
    "tired":     {PARAM_EYE_OPEN_L: 0.5, PARAM_EYE_OPEN_R: 0.5,
                  PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.3},
    "thinking":  {PARAM_EYE_LEFT_X: 0.15, PARAM_BROWS: 0.5},
    "shy":       {PARAM_MOUTH_SMILE: 0.3, PARAM_BROWS: 0.6,
                  PARAM_EYE_OPEN_L: 0.8, PARAM_EYE_OPEN_R: 0.8},
    "excited":   {PARAM_MOUTH_SMILE: 0.9, PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1,
                  PARAM_BROWS: 0.2},
    "wink":      {PARAM_EYE_OPEN_L: 0, PARAM_EYE_OPEN_R: 1, PARAM_MOUTH_SMILE: 0.4},
    "pout":      {PARAM_MOUTH_SMILE: 0, PARAM_MOUTH_OPEN: 0.2, PARAM_BROWS: 0.7},
    "sigh":      {PARAM_MOUTH_OPEN: 0.3, PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.6},
    "doubt":     {PARAM_EYE_LEFT_X: 0.1, PARAM_BROWS: 0.6, PARAM_MOUTH_SMILE: 0},
    "cry":       {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.9, PARAM_MOUTH_OPEN: 0.2,
                  PARAM_EYE_OPEN_L: 0.7, PARAM_EYE_OPEN_R: 0.7},
    "awkward":   {PARAM_MOUTH_SMILE: 0.2, PARAM_BROWS: 0.5, PARAM_EYE_LEFT_X: 0.08},
}

DEFAULT_EMOTION_KEYWORDS: dict[str, str] = {
    "开心": "happy", "高兴": "happy", "快乐": "happy", "愉悦": "happy",
    "难过": "sad", "伤心": "sad", "悲伤": "sad", "沮丧": "sad",
    "生气": "angry", "愤怒": "angry", "恼火": "angry", "烦躁": "angry",
    "惊讶": "surprised", "震惊": "surprised", "意外": "surprised",
    "疲惫": "tired", "累": "tired", "疲倦": "tired", "困": "tired",
    "思考": "thinking", "思索": "thinking", "琢磨": "thinking",
    "害羞": "shy", "不好意思": "shy",
    "兴奋": "excited", "激动": "excited",
    "疑惑": "doubt", "怀疑": "doubt", "困惑": "doubt",
    "无奈": "sigh", "叹息": "sigh",
    "尴尬": "awkward", "窘": "awkward",
}

DEFAULT_LIPSYNC_CONFIG = {
    "enabled": True,
    "energy_multiplier": 3.0,
    "smooth_factor": 0.7,
    "mouth_open_min": 0.05,
    "mouth_open_max": 0.85,
    "mouth_smile_amount": 0.15,
}

