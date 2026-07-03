"""Motion-script evaluation helpers for VTS body driver."""

from __future__ import annotations

import math
from typing import Any

from kokoro.action.tools.vts.body_utils import _clamp, _float
from kokoro.action.tools.vts.controller_params import (
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
)


def _motion_to_params(motion: dict[str, Any], t: float, envelope: float, *, channel: str, energy: float) -> dict[str, float]:
    target = str(motion.get("target") or "").lower()
    kind = str(motion.get("kind") or "").lower()
    raw_amp = _float(motion.get("amplitude"), 1.0)
    amp = raw_amp * max(0.1, energy) * envelope
    freq = max(0.05, _float(motion.get("frequency"), 0.5))
    phase = _float(motion.get("phase"), 0.0)
    wave = math.sin((t * freq + phase) * math.tau)
    value = _float(motion.get("value"), 0.0)
    out: dict[str, float] = {}

    if channel == "body":
        amp = raw_amp * (0.75 + min(1.0, max(0.0, energy)) * 0.45) * envelope
        if target == "head" and kind in {"sway", "shake"}:
            axis = str(motion.get("axis") or "x").lower()
            param = PARAM_FACE_ANGLE_X if axis == "x" else PARAM_FACE_ANGLE_Y if axis == "y" else PARAM_FACE_ANGLE_Z
            out[param] = wave * amp
            if axis == "x":
                out[PARAM_MOCOPI_BODY_ANGLE_X] = wave * amp * 0.7
                out[PARAM_MOCOPI_BODY_POS_X] = wave * min(1.0, amp * 0.11)
            elif axis == "y":
                out[PARAM_MOCOPI_BODY_ANGLE_Y] = wave * amp * 0.45
                out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.8, amp * 0.06)
            else:
                out[PARAM_MOCOPI_BODY_ANGLE_Z] = wave * amp * 0.9
        elif target == "head" and kind == "nod":
            out[PARAM_FACE_ANGLE_Y] = wave * amp
            out[PARAM_MOCOPI_BODY_ANGLE_Y] = wave * amp * 0.55
            out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.8, amp * 0.06)
        elif target == "head" and kind in {"tilt", "droop"}:
            out[PARAM_FACE_ANGLE_Z] = value * envelope if value else amp
            out[PARAM_MOCOPI_BODY_ANGLE_Z] = (value * 0.6 if value else amp * 0.6) * envelope
            if kind == "droop":
                out[PARAM_FACE_ANGLE_Y] = -abs(amp)
                out[PARAM_MOCOPI_BODY_ANGLE_Y] = -abs(amp) * 0.45
        elif target == "body" and kind in {"breath", "bounce"}:
            vertical = wave * min(1.8, amp * 0.45)
            out[PARAM_FACE_POS_Y] = vertical
            out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.9, amp * 0.22)
        return out

    if channel == "face":
        if target == "mouth" and kind in {"smile", "pout"}:
            out[PARAM_MOUTH_SMILE] = (value if value else amp) * (1 if kind == "smile" else -1)
        elif target == "mouth" and kind == "open":
            out[PARAM_MOUTH_OPEN] = abs(value if value else amp) * envelope
        elif target == "eyes" and kind in {"squint", "sleepy"}:
            openness = max(0.15, 1.0 - abs(value if value else amp))
            out[PARAM_EYE_OPEN_L] = openness
            out[PARAM_EYE_OPEN_R] = openness
        elif target == "eyes" and kind == "look":
            x = _float(motion.get("x"), wave * amp * 0.15)
            y = _float(motion.get("y"), 0.0)
            out[PARAM_EYE_LEFT_X] = x * envelope
            out[PARAM_EYE_RIGHT_X] = x * envelope
            out[PARAM_EYE_LEFT_Y] = y * envelope
            out[PARAM_EYE_RIGHT_Y] = y * envelope
        elif target == "eyes" and kind in {"blink", "soft_blink"}:
            interval = max(1.0, _float(motion.get("interval"), 3.0))
            blink_phase = (t + phase) % interval
            if blink_phase < 0.08:
                out[PARAM_EYE_OPEN_L] = 0.0
                out[PARAM_EYE_OPEN_R] = 0.0
        elif target == "brows" and kind in {"raise", "frown"}:
            out[PARAM_BROWS] = (value if value else amp) * (1 if kind == "frown" else -1)
    return out


def _smooth_params(current: dict[str, float], target: dict[str, float], *, alpha: float) -> dict[str, float]:
    keys = set(current) | set(target)
    out: dict[str, float] = {}
    for key in keys:
        cv = float(current.get(key, 0.0))
        tv = float(target.get(key, 0.0))
        value = cv + (tv - cv) * alpha
        if abs(value) > 0.002:
            out[key] = value
    return out


def _envelope(elapsed: float, duration: float, *, hold_tail: bool = False) -> float:
    fade = min(2.4, max(1.2, duration * 0.34))
    if elapsed < fade:
        return _ease(elapsed / max(0.001, fade))
    if hold_tail:
        return 1.0
    if elapsed > duration - fade:
        return _ease(max(0.0, (duration - elapsed) / max(0.001, fade)))
    return 1.0


def _ease(x: float) -> float:
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _clamp_params(params: dict[str, float]) -> dict[str, float]:
    params = {k: v for k, v in params.items() if k not in {PARAM_FACE_POS_Z, PARAM_MOCOPI_BODY_POS_Z}}
    ranges = {
        PARAM_EYE_OPEN_L: (0.0, 1.2),
        PARAM_EYE_OPEN_R: (0.0, 1.2),
        PARAM_MOUTH_OPEN: (0.0, 0.9),
        PARAM_MOUTH_SMILE: (-0.8, 1.0),
        PARAM_BROWS: (-1.0, 1.0),
        PARAM_FACE_ANGLE_X: (-10.0, 10.0),
        PARAM_FACE_ANGLE_Y: (-8.0, 8.0),
        PARAM_FACE_ANGLE_Z: (-12.0, 12.0),
        PARAM_FACE_POS_X: (-4.0, 4.0),
        PARAM_FACE_POS_Y: (-4.0, 4.0),
        PARAM_FACE_POS_Z: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_ANGLE_X: (-12.0, 12.0),
        PARAM_MOCOPI_BODY_ANGLE_Y: (-10.0, 10.0),
        PARAM_MOCOPI_BODY_ANGLE_Z: (-12.0, 12.0),
        PARAM_MOCOPI_BODY_POS_X: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_POS_Y: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_POS_Z: (-1.0, 1.0),
        PARAM_EYE_LEFT_X: (-0.6, 0.6),
        PARAM_EYE_RIGHT_X: (-0.6, 0.6),
        PARAM_EYE_LEFT_Y: (-0.6, 0.6),
        PARAM_EYE_RIGHT_Y: (-0.6, 0.6),
    }
    return {key: _clamp(float(value), *ranges.get(key, (-1.0, 1.0))) for key, value in params.items()}
