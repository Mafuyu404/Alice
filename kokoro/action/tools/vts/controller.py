"""VTube Studio integration public facade."""

from kokoro.action.tools.vts.controller_arbiter import VTSExpressionArbiter
from kokoro.action.tools.vts.controller_core import VTSController, _load_vts_mapping
from kokoro.action.tools.vts.controller_idle import VTSIdleLoop
from kokoro.action.tools.vts.controller_lipsync import VTSLipSync
from kokoro.action.tools.vts.controller_params import (
    DEFAULT_EMOTION_KEYWORDS,
    DEFAULT_EXPRESSIONS,
    DEFAULT_LIPSYNC_CONFIG,
    DEVELOPER,
    PARAM_BROWS,
    PARAM_EYE_LEFT_X,
    PARAM_EYE_LEFT_Y,
    PARAM_EYE_OPEN_L,
    PARAM_EYE_OPEN_R,
    PARAM_EYE_RIGHT_X,
    PARAM_EYE_RIGHT_Y,
    PARAM_FACE_ANGLE_X,
    PARAM_FACE_ANGLE_Y,
    PARAM_FACE_ANGLE_Z,
    PARAM_FACE_POS_X,
    PARAM_FACE_POS_Y,
    PARAM_FACE_POS_Z,
    PARAM_MOCOPI_BODY_ANGLE_X,
    PARAM_MOCOPI_BODY_ANGLE_Y,
    PARAM_MOCOPI_BODY_ANGLE_Z,
    PARAM_MOCOPI_BODY_POS_X,
    PARAM_MOCOPI_BODY_POS_Y,
    PARAM_MOCOPI_BODY_POS_Z,
    PARAM_MOUTH_OPEN,
    PARAM_MOUTH_SMILE,
    PLUGIN_NAME,
    TOKEN_PATH,
    _ALL_VALID_PARAMS,
)

__all__ = [
    "DEFAULT_EMOTION_KEYWORDS",
    "DEFAULT_EXPRESSIONS",
    "DEFAULT_LIPSYNC_CONFIG",
    "DEVELOPER",
    "PLUGIN_NAME",
    "TOKEN_PATH",
    "VTSController",
    "VTSExpressionArbiter",
    "VTSIdleLoop",
    "VTSLipSync",
]
